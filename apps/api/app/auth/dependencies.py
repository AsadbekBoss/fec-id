"""Authentication and authorization dependencies.

    @router.get("/things", dependencies=[Depends(require(Permission.THINGS_READ))])
    async def list_things(...): ...

    @router.post("/things")
    async def create(ctx: Annotated[AuthContext, Depends(require(Permission.THINGS_WRITE))]): ...

Per request: verify JWT, load the session and user from the DB, and reject when the session is
revoked or expired or the user is deactivated. The role used for authorization comes from the
database, never from the token, so role changes apply immediately.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import forbidden, unauthorized
from app.auth.permissions import Permission, permissions_for
from app.auth.tokens import TokenError, decode_access_token
from app.models import AuthSession, User

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")
_TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class AuthContext:
    user: User
    session_id: uuid.UUID
    permissions: frozenset[Permission]

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def has(self, *perms: Permission) -> bool:
        return all(p in self.permissions for p in perms)


async def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise unauthorized("NOT_AUTHENTICATED", "Missing bearer token.")
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as e:
        raise unauthorized(e.code, "Access token is invalid or expired.") from None

    row = (
        await db.execute(
            select(AuthSession, User)
            .join(User, AuthSession.user_id == User.id)
            .where(AuthSession.id == claims.session_id, AuthSession.user_id == claims.user_id)
        )
    ).one_or_none()
    now = datetime.now(UTC)
    if row is None:
        raise unauthorized("SESSION_ENDED", "Session no longer exists.")
    sess, user = row
    if not sess.is_active(now) or not user.is_active:
        raise unauthorized("SESSION_ENDED", "Session has ended. Please sign in again.")

    if sess.last_used_at is None or now - sess.last_used_at > _TOUCH_INTERVAL:
        sess.last_used_at = now  # throttled write

    ctx = AuthContext(user=user, session_id=sess.id, permissions=permissions_for(user.role))
    request.state.auth = ctx
    return ctx


CurrentUser = Annotated[AuthContext, Depends(get_current_user)]


def require(*perms: Permission) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency factory: authenticated AND has every listed permission (else 403)."""

    async def _checker(ctx: CurrentUser) -> AuthContext:
        if not ctx.has(*perms):
            raise forbidden()
        return ctx

    _checker.__name__ = "require_" + "_".join(p.value.replace(":", "_") for p in perms)
    _checker.required_permissions = perms  # type: ignore[attr-defined]  # route audit test
    return _checker
