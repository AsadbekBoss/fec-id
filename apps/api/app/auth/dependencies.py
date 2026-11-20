"""Authentication and authorization dependencies.

    @router.get("/things", dependencies=[Depends(require(Permission.THINGS_READ))])
    async def list_things(...): ...

    @router.post("/things")
    async def create(ctx: Annotated[AuthContext, Depends(require(Permission.THINGS_WRITE))]): ...

Per request: verify JWT, load the session and user from the DB, and reject when the session is
revoked or expired or the user is deactivated. The role used for authorization comes from the
database, never from the token, so role changes apply immediately.

Branch scope (Phase 21): a branch user sees only `user.branch_id`. A user with
BRANCHES_MANAGE sees every branch, or one branch chosen with the `X-Branch-Id` header (the
branch switcher in the web app). Endpoints filter with `ctx.scope(Model.branch_id)` and create
rows in `ctx.write_branch`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import ColumnElement, select, true

from app.api.deps import DbSession
from app.api.errors import forbidden, unauthorized, validation_error
from app.auth.permissions import Permission, permissions_for
from app.auth.tokens import TokenError, decode_access_token
from app.models import AuthSession, Branch, User
from app.models.organization import MAIN_BRANCH_ID

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")
_TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class AuthContext:
    user: User
    session_id: uuid.UUID
    permissions: frozenset[Permission]
    # None = every branch (only with BRANCHES_MANAGE and no branch selected).
    branch_id: uuid.UUID | None = None

    @property
    def all_branches(self) -> bool:
        return Permission.BRANCHES_MANAGE in self.permissions

    @property
    def write_branch(self) -> uuid.UUID:
        """Branch new rows are created in: the selected/own branch, else the main branch."""
        return self.branch_id or MAIN_BRANCH_ID

    def scope(self, column: Any) -> ColumnElement[bool]:
        """WHERE clause limiting a query to the visible branch(es)."""
        return true() if self.branch_id is None else column == self.branch_id

    def sees(self, branch_id: uuid.UUID | None) -> bool:
        return self.branch_id is None or branch_id == self.branch_id

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

    perms = permissions_for(user.role)
    if Permission.BRANCHES_MANAGE in perms:
        branch = await _selected_branch(db, request.headers.get("x-branch-id"))
    else:
        branch = user.branch_id
        if not await db.scalar(select(Branch.is_active).where(Branch.id == branch)):
            raise unauthorized("BRANCH_INACTIVE", "This branch is closed.")
    ctx = AuthContext(user=user, session_id=sess.id, permissions=perms, branch_id=branch)
    request.state.auth = ctx
    return ctx


async def _selected_branch(db: DbSession, raw: str | None) -> uuid.UUID | None:
    """The branch chosen in the header (super administrator), or None for all branches."""
    if not raw or raw.strip().lower() in {"all", "*"}:
        return None
    try:
        bid = uuid.UUID(raw.strip())
    except ValueError:
        raise validation_error("X-Branch-Id", "invalid", "Not a branch id.") from None
    if await db.get(Branch, bid) is None:
        raise validation_error("X-Branch-Id", "not_found", "Branch not found.")
    return bid


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
