"""Excused days (Phase 24): holidays and employees' leave / sick days / business trips.

A working day that is a holiday (for everyone or one branch) becomes HOLIDAY; a working day
inside an employee's leave becomes LEAVE. Neither counts as absent, in rankings, reports or
Telegram. Someone who comes anyway is simply present (no lateness on a holiday).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey

LEAVE_KINDS = ("VACATION", "SICK", "TRIP", "OTHER")
MAX_LEAVE_DAYS = 366


class Holiday(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "holidays"

    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # NULL = every branch (set by the centre); else that branch only.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        Index(
            "uq_holidays_branch_day",
            text("coalesce(branch_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            "day",
            unique=True,
        ),
    )


class EmployeeLeave(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "employee_leaves"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in LEAVE_KINDS) + ")", name="kind_known"
        ),
        CheckConstraint("date_to >= date_from", name="range_ordered"),
        CheckConstraint(f"date_to - date_from < {MAX_LEAVE_DAYS}", name="range_bounded"),
        Index("ix_employee_leaves_employee_range", "employee_id", "date_from", "date_to"),
    )
