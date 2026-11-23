"""/api/v1/holidays and /api/v1/leaves: excused days (Phase 24).

Holidays: the centre (every-branch view) adds holidays for all branches; a branch user, or the
super administrator with a branch selected, adds holidays for that branch. Everyone sees the
all-branch holidays plus their branch's. Leave: vacation / sick / business trip / other for a
visible employee; ranges of one employee may not overlap. Everything is audited.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.api import scope
from app.api.deps import DbSession
from app.api.errors import conflict, forbidden, not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import Employee, EmployeeLeave, Holiday
from app.models.absences import MAX_LEAVE_DAYS
from app.models.enums import ActorType
from app.schemas.common import StrictModel

router = APIRouter(tags=["holidays & leave"])
Reader = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_WRITE))]
LeaveKind = Literal["VACATION", "SICK", "TRIP", "OTHER"]


async def _audit(
    db: DbSession,
    ctx: AuthContext,
    request: Request,
    action: str,
    entity: str,
    entity_id: uuid.UUID,
    changes: dict[str, Any],
) -> None:
    await audit.record(
        db,
        action=action,
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type=entity,
        entity_id=entity_id,
        ip=client_ip(request),
        changes=changes,
    )


# --- holidays -----------------------------------------------------------------------------------


class HolidayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    day: date
    name: str
    branch_id: uuid.UUID | None  # None = every branch
    editable: bool = False


class HolidayIn(StrictModel):
    day: date
    name: str = Field(min_length=1, max_length=120)


def _holiday_scope(ctx: AuthContext) -> Any:
    if ctx.branch_id is None:
        return Holiday.id.is_not(None)  # every holiday
    return or_(Holiday.branch_id.is_(None), Holiday.branch_id == ctx.branch_id)


def _editable(ctx: AuthContext, h: Holiday) -> bool:
    """The centre edits everything; a branch view edits that branch's holidays only."""
    return ctx.branch_id is None if h.branch_id is None else ctx.sees(h.branch_id)


def _hout(ctx: AuthContext, h: Holiday) -> HolidayOut:
    out = HolidayOut.model_validate(h)
    out.editable = _editable(ctx, h)
    return out


@router.get("/holidays", response_model=list[HolidayOut], summary="Holidays of a year")
async def list_holidays(
    ctx: Reader, db: DbSession, year: Annotated[int, Query(ge=2000, le=2100)]
) -> list[HolidayOut]:
    rows = await db.execute(
        select(Holiday)
        .where(Holiday.day.between(date(year, 1, 1), date(year, 12, 31)), _holiday_scope(ctx))
        .order_by(Holiday.day, Holiday.name)
    )
    return [_hout(ctx, h) for h in rows.scalars()]


@router.post(
    "/holidays",
    response_model=HolidayOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {}, 422: {}},
    summary="Add a holiday (every branch in the centre view, else the shown branch)",
)
async def create_holiday(
    body: HolidayIn, ctx: Writer, request: Request, db: DbSession
) -> HolidayOut:
    h = Holiday(
        day=body.day,
        name=" ".join(body.name.split()),
        branch_id=ctx.branch_id,
        created_by_user_id=ctx.user_id,
    )
    db.add(h)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict("HOLIDAY_EXISTS", "This day is already a holiday.") from None
    await _audit(
        db,
        ctx,
        request,
        "HOLIDAY_CREATED",
        "holiday",
        h.id,
        {
            "day": [None, h.day.isoformat()],
            "name": [None, h.name],
            "branch_id": [None, str(h.branch_id)],
        },
    )
    return _hout(ctx, h)


@router.delete(
    "/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {}, 404: {}},
    summary="Delete a holiday",
)
async def delete_holiday(
    holiday_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession
) -> None:
    h = (
        await db.execute(select(Holiday).where(Holiday.id == holiday_id, _holiday_scope(ctx)))
    ).scalar_one_or_none()
    if h is None:
        raise not_found("Holiday")
    if not _editable(ctx, h):
        raise forbidden("Holidays of every branch are managed by the centre.")
    await _audit(
        db, ctx, request, "HOLIDAY_DELETED", "holiday", h.id, {"day": [h.day.isoformat(), None]}
    )
    await db.delete(h)


# --- leave --------------------------------------------------------------------------------------


class LeaveIn(StrictModel):
    employee_id: uuid.UUID
    kind: LeaveKind
    date_from: date
    date_to: date
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _range(self) -> LeaveIn:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        if (self.date_to - self.date_from).days >= MAX_LEAVE_DAYS:
            raise ValueError(f"at most {MAX_LEAVE_DAYS} days")
        return self


class LeaveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    employee_name: str = ""
    employee_code: str = ""
    kind: str
    date_from: date
    date_to: date
    note: str | None


def _lout(lv: EmployeeLeave, e: Employee) -> LeaveOut:
    out = LeaveOut.model_validate(lv)
    out.employee_name = f"{e.last_name} {e.first_name}"
    out.employee_code = e.employee_code
    return out


@router.get("/leaves", response_model=list[LeaveOut], summary="Leave overlapping a date range")
async def list_leaves(
    ctx: Reader,
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    employee_id: uuid.UUID | None = None,
) -> list[LeaveOut]:
    stmt = (
        select(EmployeeLeave, Employee)
        .join(Employee, Employee.id == EmployeeLeave.employee_id)
        .where(Employee.deleted_at.is_(None), ctx.scope(Employee.branch_id))
    )
    if employee_id is not None:
        stmt = stmt.where(EmployeeLeave.employee_id == employee_id)
    if date_from is not None:
        stmt = stmt.where(EmployeeLeave.date_to >= date_from)
    if date_to is not None:
        stmt = stmt.where(EmployeeLeave.date_from <= date_to)
    rows = await db.execute(stmt.order_by(EmployeeLeave.date_from.desc()).limit(500))
    return [_lout(lv, e) for lv, e in rows.all()]


@router.post(
    "/leaves",
    response_model=LeaveOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {}, 409: {"description": "LEAVE_OVERLAP"}, 422: {}},
    summary="Record leave / sick days / a business trip",
)
async def create_leave(body: LeaveIn, ctx: Writer, request: Request, db: DbSession) -> LeaveOut:
    emp = await scope.employee(db, ctx, body.employee_id)
    clash = (
        (
            await db.execute(
                select(EmployeeLeave).where(
                    EmployeeLeave.employee_id == emp.id,
                    EmployeeLeave.date_from <= body.date_to,
                    EmployeeLeave.date_to >= body.date_from,
                )
            )
        )
        .scalars()
        .first()
    )
    if clash is not None:
        raise conflict(
            "LEAVE_OVERLAP",
            f"Overlaps an existing record {clash.date_from.isoformat()} – "  # noqa: RUF001
            f"{clash.date_to.isoformat()}.",
        )
    if body.date_from < emp.hire_date:
        raise validation_error("date_from", "before_hire", "Leave cannot start before hiring.")
    lv = EmployeeLeave(
        employee_id=emp.id,
        kind=body.kind,
        date_from=body.date_from,
        date_to=body.date_to,
        note=(body.note or "").strip() or None,
        created_by_user_id=ctx.user_id,
    )
    db.add(lv)
    await db.flush()
    await _audit(
        db,
        ctx,
        request,
        "LEAVE_CREATED",
        "employee",
        emp.id,
        {
            "kind": [None, lv.kind],
            "date_from": [None, lv.date_from.isoformat()],
            "date_to": [None, lv.date_to.isoformat()],
        },
    )
    return _lout(lv, emp)


@router.delete(
    "/leaves/{leave_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}},
    summary="Delete a leave record",
)
async def delete_leave(leave_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession) -> None:
    lv = await db.get(EmployeeLeave, leave_id)
    if lv is None:
        raise not_found("Leave")
    await scope.employee(db, ctx, lv.employee_id)  # other branch: 404
    await _audit(
        db,
        ctx,
        request,
        "LEAVE_DELETED",
        "employee",
        lv.employee_id,
        {"kind": [lv.kind, None], "date_from": [lv.date_from.isoformat(), None]},
    )
    await db.delete(lv)
