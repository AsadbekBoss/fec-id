"""Attendance events (camera sightings + manual/imported punches) and daily summaries."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.attendance.engine import IgnoreReason
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import (
    AttendanceStatus,
    EventSource,
    PunchDirection,
    RecognitionDecision,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.cameras import Camera
    from app.models.organization import Employee, User, WorkSchedule


class AttendanceEvent(Base):
    """One row per attendance-relevant event.

    * source=CAMERA: produced by the recognition service. `decision` is set. UNKNOWN /
      AMBIGUOUS / SPOOF rows have no employee.
    * source=MANUAL/IMPORT: entered by a user. Always has employee + direction + author.
    * `counts_for_attendance` marks events used by the daily computation (MATCHED and not
      debounced, or manual). Rows are never deleted for corrections; they are voided.
    """

    __tablename__ = "attendance_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_uid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )  # idempotency key from the producer
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[EventSource] = mapped_column(
        pg_enum(EventSource, "event_source"), nullable=False
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT")
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="RESTRICT")
    )
    decision: Mapped[RecognitionDecision | None] = mapped_column(
        pg_enum(RecognitionDecision, "recognition_decision")
    )
    direction: Mapped[PunchDirection | None] = mapped_column(
        pg_enum(PunchDirection, "punch_direction")
    )
    work_date: Mapped[date | None] = mapped_column(Date)
    counts_for_attendance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    similarity: Mapped[float | None] = mapped_column(REAL)
    second_similarity: Mapped[float | None] = mapped_column(REAL)
    liveness_score: Mapped[float | None] = mapped_column(REAL)
    quality_score: Mapped[float | None] = mapped_column(REAL)
    track_id: Mapped[str | None] = mapped_column(String(64))
    face_bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    snapshot_key: Mapped[str | None] = mapped_column(String(512))  # encrypted file reference
    model_id: Mapped[str | None] = mapped_column(String(64))
    is_voided: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    # Why a stored event does not count (engine.IgnoreReason); NULL for counted events.
    ignore_reason: Mapped[str | None] = mapped_column(String(32))
    # Groups the events added by one manual correction (= audit_logs.entity_id).
    correction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    employee: Mapped[Employee | None] = relationship(
        back_populates="attendance_events", lazy="raise"
    )
    camera: Mapped[Camera | None] = relationship(back_populates="attendance_events", lazy="raise")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id], lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "source <> 'CAMERA' OR (camera_id IS NOT NULL AND decision IS NOT NULL "
            "AND model_id IS NOT NULL)",
            name="camera_event_fields",
        ),
        CheckConstraint(
            "source = 'CAMERA' OR (employee_id IS NOT NULL AND direction IS NOT NULL "
            "AND created_by_user_id IS NOT NULL AND decision IS NULL)",
            name="manual_event_fields",
        ),
        CheckConstraint(
            "decision IS DISTINCT FROM 'MATCHED' OR employee_id IS NOT NULL",
            name="matched_has_employee",
        ),
        CheckConstraint(
            "decision IS NULL OR decision = 'MATCHED' OR employee_id IS NULL",
            name="unmatched_has_no_employee",
        ),
        CheckConstraint(
            "NOT counts_for_attendance OR (employee_id IS NOT NULL AND direction IS NOT NULL "
            "AND work_date IS NOT NULL AND (decision IS NULL OR decision = 'MATCHED'))",
            name="counted_event_complete",
        ),
        CheckConstraint("NOT is_voided OR void_reason IS NOT NULL", name="void_has_reason"),
        CheckConstraint(
            "is_voided = (voided_at IS NOT NULL) "
            "AND (voided_at IS NULL) = (voided_by_user_id IS NULL)",
            name="void_fields_together",
        ),
        CheckConstraint(
            "(counts_for_attendance AND ignore_reason IS NULL) OR "
            "(NOT counts_for_attendance AND ignore_reason IS NOT NULL)",
            name="ignore_reason_iff_not_counted",
        ),
        CheckConstraint(
            "ignore_reason IS NULL OR ignore_reason IN ("
            + ", ".join(f"'{r.value}'" for r in IgnoreReason)
            + ")",
            name="ignore_reason_known",
        ),
        CheckConstraint(
            "source <> 'MANUAL' OR correction_id IS NOT NULL", name="manual_has_correction"
        ),
        # Two counted punches of one employee can never share an instant (duplicate guard).
        Index(
            "uq_attendance_events_counted_instant",
            "employee_id",
            "event_time",
            unique=True,
            postgresql_where=text("counts_for_attendance AND NOT is_voided"),
        ),
        CheckConstraint(
            "(similarity IS NULL OR similarity BETWEEN -1 AND 1) AND "
            "(second_similarity IS NULL OR second_similarity BETWEEN -1 AND 1)",
            name="similarity_range",
        ),
        CheckConstraint(
            "(liveness_score IS NULL OR liveness_score BETWEEN 0 AND 1) AND "
            "(quality_score IS NULL OR quality_score BETWEEN 0 AND 1)",
            name="score_range",
        ),
        Index("ix_attendance_events_employee_time", "employee_id", text("event_time DESC")),
        Index("ix_attendance_events_camera_time", "camera_id", text("event_time DESC")),
        Index(
            "ix_attendance_events_counted",
            "employee_id",
            "work_date",
            postgresql_where=text("counts_for_attendance AND NOT is_voided"),
        ),
        Index(
            "ix_attendance_events_review",
            "decision",
            text("event_time DESC"),
            postgresql_where=text("decision IN ('UNKNOWN', 'AMBIGUOUS', 'SPOOF_SUSPECTED')"),
        ),
        Index("ix_attendance_events_event_time_brin", "event_time", postgresql_using="brin"),
    )


class AttendanceDaily(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "attendance_daily"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    work_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_schedules.id", ondelete="SET NULL")
    )
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worked_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    early_leave_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    overtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[AttendanceStatus] = mapped_column(
        pg_enum(AttendanceStatus, "attendance_status"), nullable=False
    )
    has_manual_events: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="attendance_days", lazy="raise")
    work_schedule: Mapped[WorkSchedule | None] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint("employee_id", "work_date"),
        Index("ix_attendance_daily_date_status", "work_date", "status"),
        CheckConstraint(
            "scheduled_minutes BETWEEN 0 AND 2880 AND worked_minutes BETWEEN 0 AND 2880 "
            "AND late_minutes BETWEEN 0 AND 2880 AND early_leave_minutes BETWEEN 0 AND 2880 "
            "AND overtime_minutes BETWEEN 0 AND 2880",
            name="minutes_range",
        ),
        CheckConstraint(
            "first_in IS NULL OR last_out IS NULL OR last_out >= first_in", name="out_after_in"
        ),
        CheckConstraint(
            "scheduled_start IS NULL OR scheduled_end IS NULL OR scheduled_end > scheduled_start",
            name="schedule_window_valid",
        ),
        CheckConstraint(
            "status NOT IN ('PRESENT', 'LATE') OR first_in IS NOT NULL", name="present_has_in"
        ),
        CheckConstraint(
            "status <> 'ABSENT' OR (first_in IS NULL AND worked_minutes = 0)",
            name="absent_has_no_work",
        ),
    )
