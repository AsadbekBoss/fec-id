"""Telegram notification recipients and the log that makes every notification send at most once."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey

# What a recipient can subscribe to. Admin kinds are about everyone; self kinds go only to the
# chat linked to that employee and only about that employee.
ADMIN_SUBSCRIPTIONS = ("daily", "late", "reviews", "system", "director")
# Only for centre chats (branch_id NULL): it names people of every branch.
CENTRE_ONLY_SUBSCRIPTIONS = ("director",)
SELF_SUBSCRIPTIONS = ("self_punch", "self_late")
SUBSCRIPTIONS = ADMIN_SUBSCRIPTIONS + SELF_SUBSCRIPTIONS
DEFAULT_SUBSCRIPTIONS = ["daily", "reviews", "system"]
DEFAULT_SELF_SUBSCRIPTIONS = ["self_punch", "self_late"]


class TelegramRecipient(UUIDPrimaryKey, Timestamps, Base):
    """A Telegram chat (person or group) linked with a one-time code. `employee_id` is set when
    the chat belongs to an employee (linked from the employee page): it then receives messages
    about that employee only. One chat can be both (an administrator who is also an employee)."""

    __tablename__ = "telegram_recipients"

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    subscriptions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("""'["daily", "reviews", "system"]'::jsonb""")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    last_error: Mapped[str | None] = mapped_column(String(300))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), unique=True
    )
    # Admin chats: NULL = the centre (every branch); a branch id = that branch's news only.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (
        CheckConstraint("jsonb_typeof(subscriptions) = 'array'", name="subscriptions_is_array"),
    )


class NotificationLog(Base):
    """One row per notification (kind + key unique): inserted BEFORE sending, so a restart or a
    second worker never sends the same message twice."""

    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    recipients: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    delivered: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    summary: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    __table_args__ = (UniqueConstraint("kind", "key"),)  # uq_notification_log_kind_key
