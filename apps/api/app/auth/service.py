"""Authentication use cases. Every function runs inside the caller's transaction.

Failures that must be persisted (failed-attempt counters, lockouts, token-reuse revocation)
return a failure value instead of raising, so the request transaction still commits them.
Raising would roll them back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords, tokens
from app.core.config import get_settings
from app.db import audit
from app.models import AuditLog, AuthSession, RefreshToken, User
from app.models.enums import ActorType


class FailureReason(StrEnum):
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    RATE_LIMITED = "RATE_LIMITED"  # too many failures from this IP or account temporarily locked
    INVALID_REFRESH = "INVALID_REFRESH"
    TOKEN_REUSE = "TOKEN_REUSE"  # noqa: S105 (enum label, not a secret)


@dataclass(frozen=True)
class Failure:
    reason: FailureReason
    retry_after: int = 0


@dataclass(frozen=True)
class IssuedTokens:
    user: User
    session: AuthSession
    access_token: str
    expires_in: int
    refresh_token: str


def _now() -> datetime:
    return datetime.now(UTC)


async def _new_refresh(db: AsyncSession, sess: AuthSession) -> str:
    raw, digest = tokens.new_refresh_token()
    db.add(RefreshToken(session_id=sess.id, token_hash=digest, expires_at=sess.expires_at))
    return raw


async def _ip_failures(db: AsyncSession, ip: str | None, window_minutes: int) -> int:
    if not ip:
        return 0
    since = _now() - timedelta(minutes=window_minutes)
    return (
        await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "AUTH_LOGIN_FAILED",
                AuditLog.ip == ip,
                AuditLog.created_at >= since,
            )
        )
    ).scalar_one()


async def login(
    db: AsyncSession, username: str, password: str, ip: str | None, user_agent: str | None
) -> IssuedTokens | Failure:
    s = get_settings()
    now = _now()

    # 1. Per-IP failure limit (DB-backed, so it works across processes).
    if await _ip_failures(db, ip, s.login_ip_window_minutes) >= s.login_ip_max_failures:
        await audit.record(
            db,
            action="AUTH_LOGIN_BLOCKED",
            actor_type=ActorType.SYSTEM,
            ip=ip,
            user_agent=user_agent,
            changes={"reason": "ip_limit"},
        )
        return Failure(FailureReason.RATE_LIMITED, s.login_ip_window_minutes * 60)

    # 2. Load the account and lock its row, so concurrent attempts count correctly.
    user = (
        await db.execute(select(User).where(User.username == username).with_for_update())
    ).scalar_one_or_none()

    # 3. Temporarily locked account: reject WITHOUT checking the password (stops guessing).
    if user is not None and user.locked_until is not None and user.locked_until > now:
        await audit.record(
            db,
            action="AUTH_LOGIN_BLOCKED",
            actor_type=ActorType.SYSTEM,
            ip=ip,
            user_agent=user_agent,
            entity_type="user",
            entity_id=user.id,
            changes={"reason": "account_locked"},
        )
        return Failure(
            FailureReason.RATE_LIMITED, int((user.locked_until - now).total_seconds()) + 1
        )

    # 4. Constant-work password check (dummy hash when the user does not exist).
    ok = await passwords.verify_password(user.password_hash if user else None, password)

    if not ok or user is None or not user.is_active:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= s.login_max_failures:
                user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
                user.failed_login_count = 0
        await audit.record(
            db,
            action="AUTH_LOGIN_FAILED",
            actor_type=ActorType.SYSTEM,
            ip=ip,
            user_agent=user_agent,
            # The attempted username is NOT stored for unknown accounts (it could be a password
            # typed into the wrong field).
            entity_type="user" if user else None,
            entity_id=user.id if user else None,
            changes={"locked": bool(user and user.locked_until and user.locked_until > now)},
        )
        return Failure(FailureReason.INVALID_CREDENTIALS)

    # 5. Success.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if passwords.needs_rehash(user.password_hash):  # parameters were strengthened
        user.password_hash = await passwords.hash_password(password)

    sess = AuthSession(
        user_id=user.id,
        expires_at=now + timedelta(days=s.session_days),
        last_used_at=now,
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(sess)
    await db.flush()
    refresh = await _new_refresh(db, sess)
    access, expires_in = tokens.issue_access_token(user.id, sess.id, user.role.value, now)
    await audit.record(
        db,
        action="AUTH_LOGIN_SUCCEEDED",
        actor_type=ActorType.USER,
        actor_user_id=user.id,
        entity_type="auth_session",
        entity_id=sess.id,
        ip=ip,
        user_agent=user_agent,
    )
    return IssuedTokens(user, sess, access, expires_in, refresh)


async def refresh(
    db: AsyncSession, raw_token: str, ip: str | None, user_agent: str | None
) -> IssuedTokens | Failure:
    now = _now()
    row = (
        await db.execute(
            select(RefreshToken, AuthSession, User)
            .join(AuthSession, RefreshToken.session_id == AuthSession.id)
            .join(User, AuthSession.user_id == User.id)
            .where(RefreshToken.token_hash == tokens.hash_refresh_token(raw_token))
            .with_for_update(of=[RefreshToken, AuthSession])
        )
    ).one_or_none()
    if row is None:
        return Failure(FailureReason.INVALID_REFRESH)
    token, sess, user = row

    if token.used_at is not None:
        # A rotated token came back: it was copied. Kill the whole session (both the thief's
        # and the victim's copies stop working).
        if sess.revoked_at is None:
            sess.revoked_at, sess.revoke_reason = now, "TOKEN_REUSE"
        await audit.record(
            db,
            action="AUTH_REFRESH_REUSE_DETECTED",
            actor_type=ActorType.SYSTEM,
            ip=ip,
            user_agent=user_agent,
            entity_type="auth_session",
            entity_id=sess.id,
        )
        return Failure(FailureReason.TOKEN_REUSE)

    if not sess.is_active(now) or token.expires_at <= now or not user.is_active:
        return Failure(FailureReason.INVALID_REFRESH)

    token.used_at = now
    sess.last_used_at = now
    new_raw = await _new_refresh(db, sess)
    access, expires_in = tokens.issue_access_token(user.id, sess.id, user.role.value, now)
    return IssuedTokens(user, sess, access, expires_in, new_raw)


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoke_reason=reason)
    )


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, reason: str, except_session: uuid.UUID | None = None
) -> None:
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoke_reason=reason)
    )
    if except_session is not None:
        stmt = stmt.where(AuthSession.id != except_session)
    await db.execute(stmt)


async def change_password(
    db: AsyncSession,
    user: User,
    current_session: uuid.UUID,
    current: str,
    new: str,
    ip: str | None,
) -> list[str] | None:
    """Returns a list of policy problems, or None on success. A wrong current password is
    reported as a problem (same as policy) so the endpoint returns 422, not 401: the caller is
    authenticated, and a 401 would make the client think the session ended."""
    if not await passwords.verify_password(user.password_hash, current):
        return ["Current password is incorrect."]
    problems = passwords.policy_violations(new, user.username)
    if passwords.normalize(new) == passwords.normalize(current):
        problems.append("New password must differ from the current password.")
    if problems:
        return problems
    user.password_hash = await passwords.hash_password(new)
    user.password_changed_at = _now()
    await revoke_user_sessions(db, user.id, "PASSWORD_CHANGED", except_session=current_session)
    await audit.record(
        db,
        action="AUTH_PASSWORD_CHANGED",
        actor_type=ActorType.USER,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip=ip,
    )
    return None
