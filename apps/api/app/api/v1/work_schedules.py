"""/api/v1/work-schedules: the organisation's configured working hours."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.errors import conflict, not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import Department, Employee, WorkSchedule
from app.models.enums import ActorType
from app.schemas.attendance import (
    WorkScheduleCreate,
    WorkScheduleOut,
    WorkScheduleUpdate,
    _check_shape,
)

router = APIRouter(prefix="/work-schedules", tags=["work schedules"])

Reader = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_WRITE))]


def _out(ws: WorkSchedule) -> WorkScheduleOut:
    out = WorkScheduleOut.model_validate(ws)
    out.crosses_midnight = ws.end_time <= ws.start_time
    return out


async def _get(db: DbSession, schedule_id: uuid.UUID, lock: bool = False) -> WorkSchedule:
    stmt = select(WorkSchedule).where(WorkSchedule.id == schedule_id)
    if lock:
        stmt = stmt.with_for_update()
    ws = (await db.execute(stmt)).scalar_one_or_none()
    if ws is None:
        raise not_found("Work schedule")
    return ws


async def _flush(db: DbSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict(
            "SCHEDULE_EXISTS", "A work schedule with this name already exists."
        ) from None


def _plain(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("", response_model=list[WorkScheduleOut], summary="List work schedules")
async def list_schedules(_: Reader, db: DbSession) -> list[WorkScheduleOut]:
    rows = (await db.execute(select(WorkSchedule).order_by(WorkSchedule.name))).scalars()
    return [_out(w) for w in rows]


@router.get("/{schedule_id}", response_model=WorkScheduleOut, responses={404: {}})
async def get_schedule(schedule_id: uuid.UUID, _: Reader, db: DbSession) -> WorkScheduleOut:
    return _out(await _get(db, schedule_id))


@router.post(
    "",
    response_model=WorkScheduleOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {}, 422: {}},
    summary="Create work schedule",
)
async def create_schedule(
    body: WorkScheduleCreate, ctx: Writer, request: Request, db: DbSession
) -> WorkScheduleOut:
    ws = WorkSchedule(**body.model_dump())
    db.add(ws)
    await _flush(db)
    await audit.record(
        db,
        action="WORK_SCHEDULE_CREATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="work_schedule",
        entity_id=ws.id,
        ip=client_ip(request),
        changes={k: [None, _plain(v)] for k, v in body.model_dump().items()},
    )
    await db.refresh(ws)
    return _out(ws)


@router.patch(
    "/{schedule_id}",
    response_model=WorkScheduleOut,
    responses={404: {}, 409: {}, 422: {}},
    summary="Update work schedule",
)
async def update_schedule(
    schedule_id: uuid.UUID,
    body: WorkScheduleUpdate,
    ctx: Writer,
    request: Request,
    db: DbSession,
) -> WorkScheduleOut:
    ws = await _get(db, schedule_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    try:
        _check_shape(
            changes.get("start_time", ws.start_time),
            changes.get("end_time", ws.end_time),
            changes.get("break_minutes", ws.break_minutes),
        )
    except ValueError as e:
        raise validation_error("start_time", "invalid_shape", str(e)) from None
    diff = {}
    for k, v in changes.items():
        if getattr(ws, k) != v:
            diff[k] = [_plain(getattr(ws, k)), _plain(v)]
            setattr(ws, k, v)
    await _flush(db)
    if diff:
        await audit.record(
            db,
            action="WORK_SCHEDULE_UPDATED",
            actor_type=ActorType.USER,
            actor_user_id=ctx.user_id,
            entity_type="work_schedule",
            entity_id=ws.id,
            ip=client_ip(request),
            changes=diff,
        )
    await db.refresh(ws)
    return _out(ws)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}, 409: {"description": "Schedule is assigned"}},
    summary="Delete an unassigned work schedule",
)
async def delete_schedule(
    schedule_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession
) -> None:
    ws = await _get(db, schedule_id, lock=True)
    used = (
        await db.execute(
            select(func.count())
            .select_from(Employee)
            .where(Employee.work_schedule_id == ws.id, Employee.deleted_at.is_(None))
        )
    ).scalar_one()
    used += (
        await db.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.work_schedule_id == ws.id, Department.deleted_at.is_(None))
        )
    ).scalar_one()
    if used:
        raise conflict(
            "SCHEDULE_IN_USE",
            "Unassign the schedule from employees and departments first, or deactivate it.",
        )
    # Soft-deleted departments may still point at it (FK is RESTRICT): unlink them.
    await db.execute(
        update(Department)
        .where(Department.work_schedule_id == ws.id, Department.deleted_at.is_not(None))
        .values(work_schedule_id=None)
    )
    await audit.record(
        db,
        action="WORK_SCHEDULE_DELETED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="work_schedule",
        entity_id=ws.id,
        ip=client_ip(request),
        changes={"name": [ws.name, None]},
    )
    await db.delete(ws)
