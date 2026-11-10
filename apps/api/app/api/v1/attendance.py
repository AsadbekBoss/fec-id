"""/api/v1/attendance: events, corrections, daily results, board, recompute."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.api.errors import AppError, not_found, validation_error
from app.attendance import engine
from app.attendance import service as att
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import AttendanceEvent, Employee
from app.models.enums import ActorType, EventSource
from app.schemas.attendance import (
    BoardOut,
    CameraEventIn,
    CorrectionIn,
    CorrectionOut,
    DayDetail,
    DayOut,
    EmployeeBrief,
    EventOut,
    IngestOut,
    RecomputeIn,
    RecomputeOut,
    ScheduleRef,
)
from app.schemas.common import Page
from app.services.employees import department_subtree_ids

router = APIRouter(prefix="/attendance", tags=["attendance"])

Reader = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_WRITE))]
MAX_RANGE_DAYS = 31


# --- helpers ----------------------------------------------------------------------------------


def _full_name(e: Employee) -> str:
    return " ".join([e.last_name, e.first_name] + ([e.middle_name] if e.middle_name else []))


def day_out(emp: Employee, r: engine.DayResult) -> DayOut:
    return DayOut(
        employee=EmployeeBrief(
            id=emp.id,
            employee_code=emp.employee_code,
            full_name=_full_name(emp),
            department_id=emp.department_id,
        ),
        work_date=r.work_date,
        status=r.status.value,
        in_progress=r.in_progress,
        schedule=ScheduleRef(id=r.schedule.id, name=r.schedule.name) if r.schedule else None,
        scheduled_start=r.scheduled_start,
        scheduled_end=r.scheduled_end,
        scheduled_minutes=r.scheduled_minutes,
        arrival=r.first_in,
        departure=r.last_out,
        worked_minutes=r.worked_minutes,
        late_minutes=r.late_minutes,
        early_leave_minutes=r.early_leave_minutes,
        overtime_minutes=r.overtime_minutes,
        has_manual_events=r.has_manual_events,
    )


def _check_range(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise validation_error("date_to", "before_from", "date_to must be on or after date_from.")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise validation_error(
            "date_to", "range_too_long", f"At most {MAX_RANGE_DAYS} days per request."
        )


async def _employees(
    db: DbSession,
    employee_id: uuid.UUID | None,
    department_id: uuid.UUID | None,
    include_subdepartments: bool = True,
) -> list[Employee]:
    stmt = select(Employee).where(Employee.deleted_at.is_(None))
    if employee_id is not None:
        stmt = stmt.where(Employee.id == employee_id)
    if department_id is not None:
        ids = (
            await department_subtree_ids(db, department_id)
            if include_subdepartments
            else [department_id]
        )
        stmt = stmt.where(Employee.department_id.in_(ids or [department_id]))
    return list(
        (
            await db.execute(stmt.order_by(Employee.last_name, Employee.first_name, Employee.id))
        ).scalars()
    )


def _ev(e: AttendanceEvent) -> EventOut:
    return EventOut.model_validate(e)


# --- events -----------------------------------------------------------------------------------


@router.post(
    "/events",
    response_model=IngestOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Idempotent replay: event_uid already stored"}, 422: {}},
    summary="Ingest a recognition/terminal event (simulation until cameras are connected)",
)
async def ingest_event(
    body: CameraEventIn, ctx: Writer, response: Response, db: DbSession
) -> IngestOut:
    try:
        result = await att.ingest_camera_event(db, **body.model_dump())
    except att.EventRejected as e:
        raise validation_error(e.field, e.code, e.message) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
        return IngestOut(event=_ev(result.event), day=None)
    day = None
    if result.day is not None and result.event.employee_id is not None:
        emp = await db.get(Employee, result.event.employee_id)
        if emp is not None:
            day = day_out(emp, result.day)
    return IngestOut(event=_ev(result.event), day=day)


@router.get("/events", response_model=Page[EventOut], summary="Attendance event history")
async def list_events(
    _: Reader,
    db: DbSession,
    employee_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    camera_id: uuid.UUID | None = None,
    work_date_from: date | None = None,
    work_date_to: date | None = None,
    counted: bool | None = None,
    include_voided: bool = True,
    source: EventSource | None = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[EventOut]:
    conds: list[Any] = []
    if employee_id is not None:
        conds.append(AttendanceEvent.employee_id == employee_id)
    if department_id is not None:
        ids = await department_subtree_ids(db, department_id)
        conds.append(
            AttendanceEvent.employee_id.in_(
                select(Employee.id).where(Employee.department_id.in_(ids or [department_id]))
            )
        )
    if camera_id is not None:
        conds.append(AttendanceEvent.camera_id == camera_id)
    if work_date_from is not None:
        conds.append(AttendanceEvent.work_date >= work_date_from)
    if work_date_to is not None:
        conds.append(AttendanceEvent.work_date <= work_date_to)
    if counted is not None:
        conds.append(AttendanceEvent.counts_for_attendance.is_(counted))
    if not include_voided:
        conds.append(AttendanceEvent.is_voided.is_(False))
    if source is not None:
        conds.append(AttendanceEvent.source == source)
    total = (
        await db.execute(select(func.count()).select_from(AttendanceEvent).where(*conds))
    ).scalar_one()
    rows = (
        await db.execute(
            select(AttendanceEvent)
            .where(*conds)
            .order_by(AttendanceEvent.event_time.desc(), AttendanceEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return Page(items=[_ev(e) for e in rows], page=page, page_size=page_size, total=total)


# --- corrections ------------------------------------------------------------------------------


@router.post(
    "/corrections",
    response_model=CorrectionOut,
    responses={404: {}, 409: {"description": "INVALID_SEQUENCE"}, 422: {}},
    summary="Manually add and/or void events of one employee's work date (atomic, audited)",
)
async def correct(
    body: CorrectionIn, ctx: Writer, request: Request, db: DbSession
) -> CorrectionOut:
    ops: list[att.AddOp | att.VoidOp] = [
        att.AddOp(o.direction, o.event_time, o.note) if o.op == "add" else att.VoidOp(o.event_id)
        for o in body.operations
    ]
    try:
        result = await att.apply_correction(
            db,
            employee_id=body.employee_id,
            work_date=body.work_date,
            reason=body.reason,
            operations=ops,
            user_id=ctx.user_id,
        )
    except att.CorrectionRejected as e:
        if e.status == 404:
            raise not_found("Employee") from None
        if e.status == 409:
            raise AppError(409, e.code, "Conflict", e.message) from None
        raise validation_error(e.field, e.code, e.message) from None

    await audit.record(
        db,
        action="ATTENDANCE_CORRECTED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="attendance_correction",
        entity_id=result.correction_id,
        ip=client_ip(request),
        changes={
            "employee_id": [None, str(body.employee_id)],
            "work_date": [None, body.work_date.isoformat()],
            "reason": [None, body.reason],
            "added": [
                None,
                [
                    {
                        "id": e.id,
                        "direction": e.direction.value if e.direction else None,
                        "event_time": e.event_time.isoformat(),
                    }
                    for e in result.added
                ],
            ],
            "voided": [
                None,
                [
                    {
                        "id": e.id,
                        "direction": e.direction.value if e.direction else None,
                        "event_time": e.event_time.isoformat(),
                    }
                    for e in result.voided
                ],
            ],
            "result": [
                None,
                {"status": result.day.status.value, "worked_minutes": result.day.worked_minutes},
            ],
        },
    )
    emp = await db.get(Employee, body.employee_id)
    assert emp is not None  # noqa: S101  (locked and checked in apply_correction)
    return CorrectionOut(
        correction_id=result.correction_id,
        added=[_ev(e) for e in result.added],
        voided=[_ev(e) for e in result.voided],
        day=day_out(emp, result.day),
    )


# --- daily results ----------------------------------------------------------------------------


@router.get(
    "/days",
    response_model=Page[DayOut],
    summary="Daily attendance (computed live from events and schedules)",
)
async def list_days(
    _: Reader,
    db: DbSession,
    date_from: date,
    date_to: date,
    employee_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    include_subdepartments: bool = True,
    status_: Annotated[engine.DayStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Page[DayOut]:
    _check_range(date_from, date_to)
    emps = await _employees(db, employee_id, department_id, include_subdepartments)
    results = await att.compute_range(db, emps, date_from, date_to)
    if status_ is not None:
        results = [(e, r) for e, r in results if r.status is status_]
    results.sort(key=lambda x: (x[1].work_date, x[0].last_name, x[0].first_name), reverse=False)
    chunk = results[(page - 1) * page_size : page * page_size]
    return Page(
        items=[day_out(e, r) for e, r in chunk], page=page, page_size=page_size, total=len(results)
    )


@router.get(
    "/days/{employee_id}/{work_date}",
    response_model=DayDetail,
    responses={404: {}},
    summary="One employee's day with all its events",
)
async def get_day(employee_id: uuid.UUID, work_date: date, _: Reader, db: DbSession) -> DayDetail:
    emps = await _employees(db, employee_id, None)
    if not emps:
        raise not_found("Employee")
    results = await att.compute_range(db, emps, work_date, work_date)
    if not results:
        raise not_found("Attendance day (outside the employment period)")
    emp, r = results[0]
    events = (
        await db.execute(
            select(AttendanceEvent)
            .where(AttendanceEvent.employee_id == emp.id, AttendanceEvent.work_date == work_date)
            .order_by(AttendanceEvent.event_time, AttendanceEvent.id)
        )
    ).scalars()
    return DayDetail(**day_out(emp, r).model_dump(), events=[_ev(e) for e in events])


@router.get("/board", response_model=BoardOut, summary="Who is present / late / absent on a date")
async def board(
    _: Reader,
    db: DbSession,
    on: Annotated[date, Query(alias="date")],
    department_id: uuid.UUID | None = None,
    status_: Annotated[engine.DayStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> BoardOut:
    emps = await _employees(db, None, department_id)
    results = await att.compute_range(db, emps, on, on)
    totals = Counter(r.status.value for _, r in results)
    totals["IN_PROGRESS"] = sum(1 for _, r in results if r.in_progress)
    totals["EMPLOYEES"] = len(results)
    if status_ is not None:
        results = [(e, r) for e, r in results if r.status is status_]
    chunk = results[(page - 1) * page_size : page * page_size]
    return BoardOut(
        date=on,
        totals=dict(totals),
        items=[day_out(e, r) for e, r in chunk],
        page=page,
        page_size=page_size,
        total=len(results),
    )


@router.post(
    "/recompute",
    response_model=RecomputeOut,
    summary="Store snapshots (attendance_daily) for a range, e.g. before payroll",
)
async def recompute(
    body: RecomputeIn, ctx: Writer, request: Request, db: DbSession
) -> RecomputeOut:
    _check_range(body.date_from, body.date_to)
    emps = await _employees(db, body.employee_id, body.department_id)
    results = await att.compute_range(db, emps, body.date_from, body.date_to)
    stored = pending = 0
    for emp, r in results:
        await att.store_day(db, emp.id, r)
        if r.status is engine.DayStatus.PENDING:
            pending += 1
        else:
            stored += 1
    await audit.record(
        db,
        action="ATTENDANCE_RECOMPUTED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="attendance",
        ip=client_ip(request),
        changes={
            "date_from": [None, body.date_from.isoformat()],
            "date_to": [None, body.date_to.isoformat()],
            "stored": [None, stored],
        },
    )
    return RecomputeOut(days_stored=stored, days_pending=pending)
