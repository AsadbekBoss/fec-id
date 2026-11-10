"""/api/v1/departments: organisation tree used by employee management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.errors import conflict, not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.core.text import normalize_name
from app.db import audit
from app.models import Department, Employee, WorkSchedule
from app.models.enums import ActorType
from app.schemas.employees import DepartmentCreate, DepartmentOut, DepartmentUpdate
from app.services.employees import is_ancestor_or_self

router = APIRouter(prefix="/departments", tags=["departments"])

Reader = Annotated[AuthContext, Depends(require(Permission.EMPLOYEES_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.EMPLOYEES_WRITE))]


def _employee_count() -> Any:
    return (
        select(func.count())
        .select_from(Employee)
        .where(Employee.department_id == Department.id, Employee.deleted_at.is_(None))
        .correlate(Department)
        .scalar_subquery()
    )


async def _get(db: DbSession, dept_id: uuid.UUID) -> Department:
    d = (
        await db.execute(
            select(Department).where(Department.id == dept_id, Department.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if d is None:
        raise not_found("Department")
    return d


async def _out(db: DbSession, dept_id: uuid.UUID) -> DepartmentOut:
    row = (
        await db.execute(
            select(Department, _employee_count().label("n")).where(Department.id == dept_id)
        )
    ).one()
    out = DepartmentOut.model_validate(row.Department)
    out.employee_count = row.n
    return out


async def _flush_or_conflict(db: DbSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict(
            "DEPARTMENT_EXISTS",
            "A department with this name (under the same parent) or code already exists.",
        ) from None


@router.get("", response_model=list[DepartmentOut], summary="List departments (flat)")
async def list_departments(_: Reader, db: DbSession) -> list[DepartmentOut]:
    rows = await db.execute(
        select(Department, _employee_count().label("n"))
        .where(Department.deleted_at.is_(None))
        .order_by(Department.name)
    )
    result = []
    for row in rows:
        out = DepartmentOut.model_validate(row.Department)
        out.employee_count = row.n
        result.append(out)
    return result


@router.post(
    "",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {}, 422: {}},
    summary="Create department",
)
async def create_department(
    body: DepartmentCreate, ctx: Writer, request: Request, db: DbSession
) -> DepartmentOut:
    if body.parent_id is not None:
        await _get(db, body.parent_id)
    d = Department(name=normalize_name(body.name), code=body.code, parent_id=body.parent_id)
    db.add(d)
    await _flush_or_conflict(db)
    await audit.record(
        db,
        action="DEPARTMENT_CREATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="department",
        entity_id=d.id,
        ip=client_ip(request),
        changes={
            "name": [None, d.name],
            "parent_id": [None, str(d.parent_id) if d.parent_id else None],
        },
    )
    return await _out(db, d.id)


@router.patch(
    "/{department_id}",
    response_model=DepartmentOut,
    responses={404: {}, 409: {}, 422: {}},
    summary="Update department",
)
async def update_department(
    department_id: uuid.UUID, body: DepartmentUpdate, ctx: Writer, request: Request, db: DbSession
) -> DepartmentOut:
    d = await _get(db, department_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        if not changes["name"]:
            raise validation_error("name", "required", "name cannot be null")
        changes["name"] = normalize_name(changes["name"])
    if changes.get("parent_id") is not None:
        await _get(db, changes["parent_id"])
        if await is_ancestor_or_self(db, changes["parent_id"], d.id):
            raise validation_error(
                "parent_id", "cycle", "A department cannot be moved under itself."
            )
    if changes.get("work_schedule_id") is not None:
        if await db.get(WorkSchedule, changes["work_schedule_id"]) is None:
            raise validation_error("work_schedule_id", "not_found", "Work schedule not found.")
    if changes.get("head_employee_id") is not None:
        head = await db.get(Employee, changes["head_employee_id"])
        if head is None or head.deleted_at is not None:
            raise validation_error("head_employee_id", "not_found", "Employee not found.")
    diff = {}
    for k, v in changes.items():
        old = getattr(d, k)
        if old != v:
            diff[k] = [str(old) if old is not None else None, str(v) if v is not None else None]
            setattr(d, k, v)
    await _flush_or_conflict(db)
    if diff:
        await audit.record(
            db,
            action="DEPARTMENT_UPDATED",
            actor_type=ActorType.USER,
            actor_user_id=ctx.user_id,
            entity_type="department",
            entity_id=d.id,
            ip=client_ip(request),
            changes=diff,
        )
    return await _out(db, d.id)


@router.delete(
    "/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}, 409: {"description": "Department has employees or sub-departments"}},
    summary="Delete (soft) an empty department",
)
async def delete_department(
    department_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession
) -> None:
    d = await _get(db, department_id)
    has_staff = (
        await db.execute(
            select(func.count())
            .select_from(Employee)
            .where(Employee.department_id == d.id, Employee.deleted_at.is_(None))
        )
    ).scalar_one()
    has_children = (
        await db.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.parent_id == d.id, Department.deleted_at.is_(None))
        )
    ).scalar_one()
    if has_staff or has_children:
        raise conflict("DEPARTMENT_NOT_EMPTY", "Move employees and sub-departments first.")
    d.deleted_at = datetime.now(UTC)
    await audit.record(
        db,
        action="DEPARTMENT_DELETED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="department",
        entity_id=d.id,
        ip=client_ip(request),
    )
