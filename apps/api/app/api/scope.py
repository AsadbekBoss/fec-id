"""Branch scoping helpers (Phase 21).

Rule: a row of another branch does not exist for the caller. Loaders return 404, never 403,
so a branch user cannot even learn that an id exists elsewhere. References in request bodies
(department, schedule, camera, employee) must belong to the row's branch: else 422.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import not_found, validation_error
from app.auth.dependencies import AuthContext
from app.models import AttendanceEvent, Camera, Department, Employee, WorkSchedule


def employee_ids(ctx: AuthContext) -> Select[Any]:
    return select(Employee.id).where(ctx.scope(Employee.branch_id))


def camera_ids(ctx: AuthContext) -> Select[Any]:
    return select(Camera.id).where(ctx.scope(Camera.branch_id))


def event_scope(ctx: AuthContext) -> Any:
    """Events of the caller's employees, plus unidentified events of the caller's cameras."""
    if ctx.branch_id is None:
        return true()
    return or_(
        AttendanceEvent.employee_id.in_(employee_ids(ctx)),
        (AttendanceEvent.employee_id.is_(None) & AttendanceEvent.camera_id.in_(camera_ids(ctx))),
    )


async def employee(db: AsyncSession, ctx: AuthContext, emp_id: uuid.UUID) -> Employee:
    """A visible, non-deleted employee, else 404."""
    e = await db.get(Employee, emp_id)
    if e is None or e.deleted_at is not None or not ctx.sees(e.branch_id):
        raise not_found("Employee")
    return e


async def camera(db: AsyncSession, ctx: AuthContext, cam_id: uuid.UUID) -> Camera:
    c = await db.get(Camera, cam_id)
    if c is None or c.deleted_at is not None or not ctx.sees(c.branch_id):
        raise not_found("Camera")
    return c


async def same_branch_department(
    db: AsyncSession, branch_id: uuid.UUID, dept_id: uuid.UUID, field: str = "department_id"
) -> Department:
    d = await db.get(Department, dept_id)
    if d is None or d.deleted_at is not None or d.branch_id != branch_id:
        raise validation_error(field, "not_found", "Department not found.")
    return d


async def same_branch_schedule(
    db: AsyncSession, branch_id: uuid.UUID, schedule_id: uuid.UUID, field: str = "work_schedule_id"
) -> WorkSchedule:
    ws = await db.get(WorkSchedule, schedule_id)
    if ws is None or ws.branch_id != branch_id:
        raise validation_error(field, "not_found", "Work schedule not found.")
    return ws
