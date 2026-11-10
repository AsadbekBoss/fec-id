"""Server-side login sessions and rotating refresh tokens (ARCHITECTURE §7.1).

* One `auth_sessions` row per login. Every access token carries its `sid`; each request checks
  that the session is still active, so logout, deactivation and password change take effect
  immediately even though access tokens are JWTs.
* `refresh_tokens` stores only SHA-256 hashes of opaque random tokens. Each refresh rotates the
  token. Presenting an already-used token is treated as theft: the whole session is revoked.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKey
from app.models.organization import User


class AuthSession(UUIDPrimaryKey, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)", name="revoked_has_reason"
        ),
        CheckConstraint(
            "revoke_reason IS NULL OR revoke_reason IN "
            "('LOGOUT', 'LOGOUT_ALL', 'PASSWORD_CHANGED', 'TOKEN_REUSE', 'ADMIN', 'DEACTIVATED')",
            name="revoke_reason_known",
        ),
        Index(
            "ix_auth_sessions_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now


class RefreshToken(UUIDPrimaryKey, Base):
    __tablename__ = "refresh_tokens"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[AuthSession] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("octet_length(token_hash) = 32", name="hash_is_sha256"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )
