from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.attendance.engine import Direction
from app.models.enums import EventSource, PunchDirection, RecognitionDecision
from app.schemas.common import StrictModel

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


# --- work schedules ---------------------------------------------------------------------------


class _ScheduleRules(StrictModel):
    @field_validator("timezone", check_fields=False)
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown IANA time zone") from None
        return v

    @field_validator("working_days", check_fields=False)
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v or any(d < 1 or d > 7 for d in v) or len(set(v)) != len(v):
            raise ValueError("working_days: 1-7 unique ISO weekdays (1=Monday)")
        return sorted(v)


class WorkScheduleCreate(_ScheduleRules):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    timezone: str = "Asia/Tashkent"
    start_time: time
    end_time: time
    working_days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    break_minutes: int = Field(default=60, ge=0, le=720)
    grace_in_minutes: int = Field(default=5, ge=0, le=240)
    grace_out_minutes: int = Field(default=0, ge=0, le=240)
    early_window_minutes: int = Field(default=180, ge=0, le=720)
    late_window_minutes: int = Field(default=360, ge=0, le=720)
    overtime_enabled: bool = False

    @model_validator(mode="after")
    def _shape(self) -> WorkScheduleCreate:
        _check_shape(self.start_time, self.end_time, self.break_minutes)
        return self


def _check_shape(start: time, end: time, break_minutes: int) -> None:
    if start == end:
        raise ValueError("start_time and end_time must differ")
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    length = e - s if e > s else e + 1440 - s
    if break_minutes >= length:
        raise ValueError("break_minutes must be shorter than the shift")


class WorkScheduleUpdate(_ScheduleRules):
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
        | None
    ) = None
    timezone: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    working_days: list[int] | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=720)
    grace_in_minutes: int | None = Field(default=None, ge=0, le=240)
    grace_out_minutes: int | None = Field(default=None, ge=0, le=240)
    early_window_minutes: int | None = Field(default=None, ge=0, le=720)
    late_window_minutes: int | None = Field(default=None, ge=0, le=720)
    overtime_enabled: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> WorkScheduleUpdate:
        if not self.model_fields_set:
            raise ValueError("no fields to update")
        for f in self.model_fields_set:
            if getattr(self, f) is None:
                raise ValueError(f"{f} cannot be null")
        return self


class WorkScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
    start_time: time
    end_time: time
    working_days: list[int]
    break_minutes: int
    grace_in_minutes: int
    grace_out_minutes: int
    early_window_minutes: int
    late_window_minutes: int
    overtime_enabled: bool
    is_active: bool
    crosses_midnight: bool = False
    created_at: datetime
    updated_at: datetime


class ScheduleRef(BaseModel):
    id: uuid.UUID
    name: str


# --- events -----------------------------------------------------------------------------------


class CameraEventIn(StrictModel):
    """Event from the recognition service or a terminal. Until the recognition service exists
    (cameras are not connected yet) administrators can post simulated events."""

    event_uid: uuid.UUID = Field(description="Producer-generated idempotency key")
    camera_id: uuid.UUID
    event_time: AwareDatetime = Field(description="Capture time with UTC offset")
    decision: RecognitionDecision
    employee_id: uuid.UUID | None = None
    model_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    similarity: float | None = Field(default=None, ge=-1, le=1)
    second_similarity: float | None = Field(default=None, ge=-1, le=1)
    liveness_score: float | None = Field(default=None, ge=0, le=1)
    quality_score: float | None = Field(default=None, ge=0, le=1)
    track_id: Annotated[str, StringConstraints(max_length=64)] | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_uid: uuid.UUID
    event_time: datetime
    work_date: date | None
    source: EventSource
    employee_id: uuid.UUID | None
    camera_id: uuid.UUID | None
    decision: RecognitionDecision | None
    direction: PunchDirection | None
    counts_for_attendance: bool
    ignore_reason: str | None
    similarity: float | None
    is_voided: bool
    void_reason: str | None
    voided_at: datetime | None
    voided_by_user_id: uuid.UUID | None
    correction_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    note: str | None
    created_at: datetime = Field(description="Server receive time (trusted clock)")


# --- corrections ------------------------------------------------------------------------------


class AddOperation(StrictModel):
    op: Literal["add"]
    direction: Direction
    event_time: AwareDatetime
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class VoidOperation(StrictModel):
    op: Literal["void"]
    event_id: int = Field(gt=0)


Operation = Annotated[AddOperation | VoidOperation, Field(discriminator="op")]


class CorrectionIn(StrictModel):
    employee_id: uuid.UUID
    work_date: date
    reason: Reason
    operations: list[Operation] = Field(min_length=1, max_length=20)


# --- days -------------------------------------------------------------------------------------


class EmployeeBrief(BaseModel):
    id: uuid.UUID
    employee_code: str
    full_name: str
    department_id: uuid.UUID


class DayOut(BaseModel):
    employee: EmployeeBrief
    work_date: date
    status: str
    in_progress: bool
    schedule: ScheduleRef | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    scheduled_minutes: int
    arrival: datetime | None = Field(description="First IN")
    departure: datetime | None = Field(description="Last OUT")
    worked_minutes: int
    late_minutes: int
    early_leave_minutes: int
    overtime_minutes: int
    has_manual_events: bool


class DayDetail(DayOut):
    events: list[EventOut] = Field(description="All events of the work date, incl. ignored/voided")


class IngestOut(BaseModel):
    event: EventOut
    day: DayOut | None


class CorrectionOut(BaseModel):
    correction_id: uuid.UUID
    added: list[EventOut]
    voided: list[EventOut]
    day: DayOut


class BoardOut(BaseModel):
    date: date
    totals: dict[str, int]
    items: list[DayOut]
    page: int
    page_size: int
    total: int


class RecomputeIn(StrictModel):
    date_from: date
    date_to: date
    department_id: uuid.UUID | None = None
    employee_id: uuid.UUID | None = None


class RecomputeOut(BaseModel):
    days_stored: int
    days_pending: int
