"""Attendance use cases: load data, apply engine rules, persist results.

Concurrency: every write path locks the employee row (SELECT ... FOR UPDATE) before reading the
day's events. Two simultaneous events for one employee are therefore classified one after the
other, never against the same stale state.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import engine
from app.attendance.engine import CameraMode, Direction, IgnoreReason, Punch
from app.models import (
    AttendanceDaily,
    AttendanceEvent,
    Camera,
    Department,
    Employee,
    SystemSetting,
    WorkSchedule,
)
from app.models.enums import (
    AttendanceMethod,
    AttendanceStatus,
    EmployeeStatus,
    EventSource,
    PunchDirection,
    RecognitionDecision,
)


def now() -> datetime:
    """Single source of server time (UTC). Tests monkeypatch this."""
    return datetime.now(UTC)


# --- settings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AttendanceSettings:
    debounce: timedelta
    min_presence: timedelta
    max_event_age: timedelta
    max_clock_skew: timedelta
    default_tz: ZoneInfo


async def load_settings(db: AsyncSession) -> AttendanceSettings:
    rows: dict[str, Any] = dict(
        (
            await db.execute(
                select(SystemSetting.key, SystemSetting.value).where(
                    SystemSetting.key.like("attendance.%")
                )
            )
        ).all()
    )

    def num(key: str, default: int) -> int:
        try:
            return int(rows.get(key, default))
        except (TypeError, ValueError):
            return default

    try:
        tz = ZoneInfo(str(rows.get("attendance.default_timezone", "Asia/Tashkent")))
    except (KeyError, ValueError):
        tz = ZoneInfo("Asia/Tashkent")
    return AttendanceSettings(
        debounce=timedelta(seconds=num("attendance.debounce_seconds", 60)),
        min_presence=timedelta(minutes=num("attendance.min_presence_minutes", 30)),
        max_event_age=timedelta(hours=num("attendance.max_event_age_hours", 168)),
        max_clock_skew=timedelta(seconds=num("attendance.max_clock_skew_seconds", 120)),
        default_tz=tz,
    )


# --- schedules --------------------------------------------------------------------------------


def to_engine_schedule(ws: WorkSchedule) -> engine.Schedule:
    return engine.Schedule(
        id=ws.id,
        name=ws.name,
        tz=ZoneInfo(ws.timezone),
        start_time=ws.start_time,
        end_time=ws.end_time,
        working_days=frozenset(ws.working_days),
        break_minutes=ws.break_minutes,
        grace_in_minutes=ws.grace_in_minutes,
        grace_out_minutes=ws.grace_out_minutes,
        early_window_minutes=ws.early_window_minutes,
        late_window_minutes=ws.late_window_minutes,
        overtime_enabled=ws.overtime_enabled,
    )


class ScheduleResolver:
    """employee schedule -> department schedule -> parent department schedule -> ... -> None.
    Inactive schedules are skipped."""

    def __init__(self, schedules: list[WorkSchedule], departments: list[Department]) -> None:
        self.schedules = {s.id: to_engine_schedule(s) for s in schedules if s.is_active}
        self.departments = {d.id: d for d in departments}

    @classmethod
    async def load(cls, db: AsyncSession) -> ScheduleResolver:
        schedules = list((await db.execute(select(WorkSchedule))).scalars())
        departments = list((await db.execute(select(Department))).scalars())
        return cls(schedules, departments)

    def for_employee(self, emp: Employee) -> engine.Schedule | None:
        if emp.work_schedule_id in self.schedules:
            return self.schedules[emp.work_schedule_id]
        seen: set[uuid.UUID] = set()
        dept_id: uuid.UUID | None = emp.department_id
        while dept_id is not None and dept_id not in seen:
            seen.add(dept_id)
            dept = self.departments.get(dept_id)
            if dept is None:
                break
            if dept.work_schedule_id in self.schedules:
                return self.schedules[dept.work_schedule_id]
            dept_id = dept.parent_id
        return None


def employed_on(emp: Employee, d: date) -> bool:
    return emp.hire_date <= d and (emp.termination_date is None or d <= emp.termination_date)


# --- loading punches --------------------------------------------------------------------------


def _punch(e: AttendanceEvent) -> Punch:
    assert e.direction is not None  # noqa: S101  (counted events always have a direction: DB CHECK)
    return Punch(
        time=e.event_time,
        direction=Direction(e.direction.value),
        manual=e.source != EventSource.CAMERA,
    )


async def counted_events(
    db: AsyncSession, employee_id: uuid.UUID, work_date: date
) -> list[AttendanceEvent]:
    return list(
        (
            await db.execute(
                select(AttendanceEvent)
                .where(
                    AttendanceEvent.employee_id == employee_id,
                    AttendanceEvent.work_date == work_date,
                    AttendanceEvent.counts_for_attendance.is_(True),
                    AttendanceEvent.is_voided.is_(False),
                )
                .order_by(AttendanceEvent.event_time)
            )
        ).scalars()
    )


# --- persisting daily results -----------------------------------------------------------------


async def store_day(db: AsyncSession, employee_id: uuid.UUID, result: engine.DayResult) -> None:
    """Upsert the attendance_daily snapshot. PENDING days are not stored (and stale rows are
    removed, e.g. after a correction voided the only IN of today)."""
    if result.status is engine.DayStatus.PENDING:
        await db.execute(
            delete(AttendanceDaily).where(
                AttendanceDaily.employee_id == employee_id,
                AttendanceDaily.work_date == result.work_date,
            )
        )
        return
    values: dict[str, Any] = {
        "employee_id": employee_id,
        "work_date": result.work_date,
        "work_schedule_id": result.schedule.id if result.schedule else None,
        "scheduled_start": result.scheduled_start,
        "scheduled_end": result.scheduled_end,
        "scheduled_minutes": result.scheduled_minutes,
        "first_in": result.first_in,
        "last_out": result.last_out,
        "worked_minutes": result.worked_minutes,
        "late_minutes": result.late_minutes,
        "early_leave_minutes": result.early_leave_minutes,
        "overtime_minutes": result.overtime_minutes,
        "status": AttendanceStatus(result.status.value),
        "has_manual_events": result.has_manual_events,
        "computed_at": now(),
        "engine_version": engine.ENGINE_VERSION,
    }
    stmt = pg_insert(AttendanceDaily).values(id=uuid.uuid4(), **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AttendanceDaily.employee_id, AttendanceDaily.work_date],
        set_={k: stmt.excluded[k] for k in values if k not in ("employee_id", "work_date")},
    )
    await db.execute(stmt)


async def recompute_day(
    db: AsyncSession,
    emp: Employee,
    work_date: date,
    resolver: ScheduleResolver,
    settings: AttendanceSettings,
) -> engine.DayResult:
    events = await counted_events(db, emp.id, work_date)
    result = engine.compute_day(
        work_date,
        [_punch(e) for e in events],
        resolver.for_employee(emp),
        settings.default_tz,
        now(),
    )
    await store_day(db, emp.id, result)
    return result


# --- batched live computation (reads) ---------------------------------------------------------


async def compute_range(
    db: AsyncSession,
    employees: list[Employee],
    date_from: date,
    date_to: date,
) -> list[tuple[Employee, engine.DayResult]]:
    """Live results for every (employee, date) inside the employment period. No writes."""
    if not employees:
        return []
    settings = await load_settings(db)
    resolver = await ScheduleResolver.load(db)
    ids = [e.id for e in employees]
    rows = (
        await db.execute(
            select(AttendanceEvent)
            .where(
                AttendanceEvent.employee_id.in_(ids),
                AttendanceEvent.work_date >= date_from,
                AttendanceEvent.work_date <= date_to,
                AttendanceEvent.counts_for_attendance.is_(True),
                AttendanceEvent.is_voided.is_(False),
            )
            .order_by(AttendanceEvent.event_time)
        )
    ).scalars()
    by_key: dict[tuple[uuid.UUID, date], list[Punch]] = defaultdict(list)
    for ev in rows:
        assert ev.employee_id is not None and ev.work_date is not None  # noqa: S101
        by_key[(ev.employee_id, ev.work_date)].append(_punch(ev))

    t = now()
    out: list[tuple[Employee, engine.DayResult]] = []
    for emp in employees:
        schedule = resolver.for_employee(emp)
        d = date_from
        while d <= date_to:
            if employed_on(emp, d):
                out.append(
                    (
                        emp,
                        engine.compute_day(
                            d, by_key.get((emp.id, d), []), schedule, settings.default_tz, t
                        ),
                    )
                )
            d += timedelta(days=1)
    return out


# --- camera / terminal event ingestion --------------------------------------------------------


class EventRejected(ValueError):
    def __init__(self, field: str, code: str, message: str) -> None:
        super().__init__(message)
        self.field, self.code, self.message = field, code, message


@dataclass
class IngestResult:
    event: AttendanceEvent
    created: bool  # False = idempotent replay of an already stored event_uid
    day: engine.DayResult | None


async def serves_employee(db: AsyncSession, camera: Camera, emp: Employee) -> bool:
    """Does this camera record attendance for the employee's department? A camera with no
    departments serves everyone; assigned departments include their sub-departments."""
    if not camera.department_ids:
        return True
    from app.services.employees import department_subtree_ids  # local: avoids an import cycle

    for root in camera.department_ids:
        if emp.department_id in await department_subtree_ids(db, root):
            return True
    return False


async def ingest_camera_event(
    db: AsyncSession,
    *,
    event_uid: uuid.UUID,
    camera_id: uuid.UUID,
    event_time: datetime,
    decision: RecognitionDecision,
    employee_id: uuid.UUID | None,
    model_id: str,
    similarity: float | None = None,
    second_similarity: float | None = None,
    liveness_score: float | None = None,
    quality_score: float | None = None,
    track_id: str | None = None,
) -> IngestResult:
    settings = await load_settings(db)
    t_now = now()

    existing = (
        await db.execute(select(AttendanceEvent).where(AttendanceEvent.event_uid == event_uid))
    ).scalar_one_or_none()
    if existing is not None:
        return IngestResult(existing, created=False, day=None)

    # Reliable timestamps (business rule 7): device clock must be sane.
    if event_time.tzinfo is None:
        raise EventRejected("event_time", "naive_timestamp", "event_time must include a time zone.")
    if event_time > t_now + settings.max_clock_skew:
        raise EventRejected(
            "event_time", "in_future", "event_time is in the future (check camera clock)."
        )
    if event_time < t_now - settings.max_event_age:
        raise EventRejected("event_time", "too_old", "event_time is older than the accepted age.")

    camera = await db.get(Camera, camera_id)
    if camera is None or camera.deleted_at is not None:
        raise EventRejected("camera_id", "not_found", "Camera not found.")

    if decision is RecognitionDecision.MATCHED:
        if employee_id is None:
            raise EventRejected("employee_id", "required", "employee_id is required for MATCHED.")
    elif employee_id is not None:
        raise EventRejected("employee_id", "not_allowed", "Only MATCHED events name an employee.")

    ev = AttendanceEvent(
        event_uid=event_uid,
        event_time=event_time,
        source=EventSource.CAMERA,
        camera_id=camera.id,
        decision=decision,
        employee_id=employee_id,
        model_id=model_id,
        similarity=similarity,
        second_similarity=second_similarity,
        liveness_score=liveness_score,
        quality_score=quality_score,
        track_id=track_id,
    )

    if decision is not RecognitionDecision.MATCHED:
        ev.counts_for_attendance, ev.ignore_reason = False, IgnoreReason.UNMATCHED.value
        db.add(ev)
        await db.flush()
        return IngestResult(ev, created=True, day=None)

    emp = (
        await db.execute(
            select(Employee)
            .where(Employee.id == employee_id, Employee.deleted_at.is_(None))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if emp is None:
        raise EventRejected("employee_id", "not_found", "Employee not found.")

    resolver = await ScheduleResolver.load(db)
    schedule = resolver.for_employee(emp)
    work_date = engine.assign_work_date(event_time, schedule, settings.default_tz)
    ev.work_date = work_date

    reason: IgnoreReason | None = None
    direction: Direction | None = None
    mode = CameraMode(camera.direction.value) if camera.recognition_enabled else CameraMode.NONE
    if not employed_on(emp, work_date):
        reason = IgnoreReason.OUTSIDE_EMPLOYMENT
    elif emp.status != EmployeeStatus.ACTIVE:
        reason = IgnoreReason.EMPLOYEE_INACTIVE
    elif emp.attendance_method != AttendanceMethod.FACE:
        reason = IgnoreReason.NOT_BIOMETRIC
    elif mode is CameraMode.NONE:
        reason = IgnoreReason.MONITOR_ONLY
    elif emp.branch_id != camera.branch_id:
        reason = IgnoreReason.OTHER_BRANCH
    elif camera.department_ids and not await serves_employee(db, camera, emp):
        reason = IgnoreReason.OTHER_DEPARTMENT
    else:
        # Debounce: any earlier/later matched sighting of this employee at this camera.
        near = (
            await db.execute(
                select(AttendanceEvent.id)
                .where(
                    AttendanceEvent.employee_id == emp.id,
                    AttendanceEvent.camera_id == camera.id,
                    AttendanceEvent.is_voided.is_(False),
                    AttendanceEvent.event_time > event_time - settings.debounce,
                    AttendanceEvent.event_time < event_time + settings.debounce,
                )
                .limit(1)
            )
        ).first()
        if near:
            reason = IgnoreReason.DEBOUNCED
        else:
            counted = [_punch(e) for e in await counted_events(db, emp.id, work_date)]
            direction, reason = engine.classify_camera_punch(
                mode, event_time, counted, settings.min_presence
            )

    if reason is None and direction is not None:
        ev.counts_for_attendance = True
        ev.direction = PunchDirection(direction.value)
    else:
        ev.counts_for_attendance = False
        ev.ignore_reason = (reason or IgnoreReason.OUT_OF_SEQUENCE).value
    db.add(ev)
    await db.flush()

    day = (
        await recompute_day(db, emp, work_date, resolver, settings)
        if ev.counts_for_attendance
        else None
    )
    return IngestResult(ev, created=True, day=day)


# --- manual corrections -----------------------------------------------------------------------


@dataclass(frozen=True)
class AddOp:
    direction: Direction
    event_time: datetime
    note: str | None = None


@dataclass(frozen=True)
class VoidOp:
    event_id: int


class CorrectionRejected(ValueError):
    def __init__(self, status: int, code: str, message: str, field: str = "operations") -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


@dataclass
class CorrectionResult:
    correction_id: uuid.UUID
    added: list[AttendanceEvent]
    voided: list[AttendanceEvent]
    day: engine.DayResult


async def apply_correction(
    db: AsyncSession,
    *,
    employee_id: uuid.UUID,
    work_date: date,
    reason: str,
    operations: list[AddOp | VoidOp],
    user_id: uuid.UUID,
) -> CorrectionResult:
    """All-or-nothing: either every operation applies and the resulting day is a valid
    sequence, or nothing changes."""
    settings = await load_settings(db)
    t_now = now()
    emp = (
        await db.execute(
            select(Employee)
            .where(Employee.id == employee_id, Employee.deleted_at.is_(None))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if emp is None:
        raise CorrectionRejected(404, "NOT_FOUND", "Employee not found.", "employee_id")
    if not employed_on(emp, work_date):
        raise CorrectionRejected(
            422, "outside_employment", "work_date is outside the employment period.", "work_date"
        )
    if work_date > (t_now + timedelta(days=1)).date():
        raise CorrectionRejected(422, "in_future", "work_date is in the future.", "work_date")

    resolver = await ScheduleResolver.load(db)
    schedule = resolver.for_employee(emp)
    current = await counted_events(db, emp.id, work_date)
    by_id = {e.id: e for e in current}

    to_void: list[AttendanceEvent] = []
    for op in operations:
        if isinstance(op, VoidOp):
            ev = by_id.get(op.event_id)
            if ev is None:
                raise CorrectionRejected(
                    422,
                    "event_not_found",
                    f"Event {op.event_id} is not a counted, non-voided event of this employee "
                    "on this work date.",
                )
            if ev in to_void:
                raise CorrectionRejected(
                    422, "duplicate_operation", f"Event {op.event_id} voided twice."
                )
            to_void.append(ev)

    adds = [op for op in operations if isinstance(op, AddOp)]
    for op in adds:
        if op.event_time > t_now + settings.max_clock_skew:
            raise CorrectionRejected(422, "in_future", "Events cannot be in the future.")
        if engine.assign_work_date(op.event_time, schedule, settings.default_tz) != work_date:
            raise CorrectionRejected(
                422,
                "wrong_work_date",
                f"{op.event_time.isoformat()} does not belong to work date {work_date} "
                "under the employee's schedule.",
            )

    remaining = [_punch(e) for e in current if e not in to_void]
    proposed = sorted(
        remaining + [Punch(op.event_time, op.direction, manual=True) for op in adds],
        key=lambda p: p.time,
    )
    try:
        engine.validate_sequence(proposed)
    except engine.SequenceError as e:
        raise CorrectionRejected(
            409, "INVALID_SEQUENCE", f"Resulting day would be invalid: {e}"
        ) from None

    correction_id = uuid.uuid4()
    for ev in to_void:
        ev.is_voided = True
        ev.void_reason = reason
        ev.voided_at = t_now
        ev.voided_by_user_id = user_id
    added = []
    for op in adds:
        ev = AttendanceEvent(
            event_uid=uuid.uuid4(),
            event_time=op.event_time,
            source=EventSource.MANUAL,
            employee_id=emp.id,
            direction=PunchDirection(op.direction.value),
            work_date=work_date,
            counts_for_attendance=True,
            created_by_user_id=user_id,
            note=op.note or reason,
            correction_id=correction_id,
        )
        db.add(ev)
        added.append(ev)
    await db.flush()
    day = await recompute_day(db, emp, work_date, resolver, settings)
    return CorrectionResult(correction_id, added, to_void, day)
