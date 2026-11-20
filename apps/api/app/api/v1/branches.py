"""/api/v1/branches: filials. The super administrator opens, renames and closes them and sees an
overview of every branch; a branch user sees only its own branch here (Phase 21)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.errors import conflict, not_found, validation_error
from app.attendance import service as att
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, CurrentUser, require
from app.auth.permissions import Permission
from app.core.text import normalize_name
from app.db import audit
from app.models import Branch, Camera, Department, Employee, User, WorkSchedule
from app.models.enums import ActorType, EmployeeStatus
from app.models.organization import MAIN_BRANCH_ID
from app.schemas.common import StrictModel

router = APIRouter(prefix="/branches", tags=["branches"])
Manager = Annotated[AuthContext, Depends(require(Permission.BRANCHES_MANAGE))]


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    location: str | None
    is_active: bool
    is_main: bool = False
    employee_count: int = 0
    user_count: int = 0
    camera_count: int = 0


class BranchCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)


class BranchUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class BranchDay(BaseModel):
    branch: BranchOut
    totals: dict[str, int]


class OverviewOut(BaseModel):
    date: date
    branches: list[BranchDay]
    totals: dict[str, int]


async def _counts(db: DbSession, model: type, *conds: object) -> dict[uuid.UUID, int]:
    col = model.branch_id  # type: ignore[attr-defined]
    rows = await db.execute(select(col, func.count()).where(*conds).group_by(col))  # type: ignore[arg-type]
    return {bid: n for bid, n in rows.all()}


async def _outs(db: DbSession, branches: list[Branch]) -> list[BranchOut]:
    emps = await _counts(db, Employee, Employee.deleted_at.is_(None))
    users = await _counts(db, User, User.is_active.is_(True))
    cams = await _counts(db, Camera, Camera.deleted_at.is_(None))
    out = []
    for b in branches:
        o = BranchOut.model_validate(b)
        o.is_main = b.id == MAIN_BRANCH_ID
        o.employee_count, o.user_count = emps.get(b.id, 0), users.get(b.id, 0)
        o.camera_count = cams.get(b.id, 0)
        out.append(o)
    return out


async def _get(db: DbSession, branch_id: uuid.UUID) -> Branch:
    b = await db.get(Branch, branch_id)
    if b is None:
        raise not_found("Branch")
    return b


async def _flush(db: DbSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict("BRANCH_EXISTS", "A branch with this name already exists.") from None


async def _audit(
    db: DbSession,
    ctx: AuthContext,
    request: Request,
    action: str,
    b: Branch,
    changes: dict[str, Any],
) -> None:
    await audit.record(
        db,
        action=action,
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="branch",
        entity_id=b.id,
        ip=client_ip(request),
        changes=changes,
    )


@router.get("", response_model=list[BranchOut], summary="Branches visible to the caller")
async def list_branches(ctx: CurrentUser, db: DbSession) -> list[BranchOut]:
    stmt = select(Branch).order_by(Branch.id != MAIN_BRANCH_ID, Branch.name)
    if not ctx.all_branches:
        stmt = stmt.where(Branch.id == ctx.user.branch_id)
    return await _outs(db, list((await db.execute(stmt)).scalars()))


@router.post(
    "",
    response_model=BranchOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {}, 422: {}},
    summary="Open a branch",
)
async def create_branch(
    body: BranchCreate, ctx: Manager, request: Request, db: DbSession
) -> BranchOut:
    b = Branch(name=normalize_name(body.name), location=(body.location or "").strip() or None)
    db.add(b)
    await _flush(db)
    await _audit(db, ctx, request, "BRANCH_CREATED", b, {"name": [None, b.name]})
    await db.refresh(b)
    return (await _outs(db, [b]))[0]


@router.patch(
    "/{branch_id}",
    response_model=BranchOut,
    responses={404: {}, 409: {}, 422: {}},
    summary="Rename, move or close / reopen a branch (closing signs its users out)",
)
async def update_branch(
    branch_id: uuid.UUID, body: BranchUpdate, ctx: Manager, request: Request, db: DbSession
) -> BranchOut:
    b = await _get(db, branch_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        if not changes["name"]:
            raise validation_error("name", "required", "name cannot be null")
        changes["name"] = normalize_name(changes["name"])
    if "location" in changes:
        changes["location"] = (changes["location"] or "").strip() or None
    if changes.get("is_active") is None:
        changes.pop("is_active", None)
    elif changes["is_active"] is False and b.id == MAIN_BRANCH_ID:
        raise conflict("MAIN_BRANCH", "The main branch cannot be closed.")
    diff = {}
    for k, v in changes.items():
        if getattr(b, k) != v:
            diff[k] = [getattr(b, k), v]
            setattr(b, k, v)
    await _flush(db)
    if diff:
        await _audit(db, ctx, request, "BRANCH_UPDATED", b, diff)
    await db.refresh(b)
    return (await _outs(db, [b]))[0]


@router.delete(
    "/{branch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}, 409: {"description": "Not empty, or the main branch"}},
    summary="Delete an empty branch",
)
async def delete_branch(
    branch_id: uuid.UUID, ctx: Manager, request: Request, db: DbSession
) -> None:
    b = await _get(db, branch_id)
    if b.id == MAIN_BRANCH_ID:
        raise conflict("MAIN_BRANCH", "The main branch cannot be deleted.")
    # Deleted (soft) rows still reference the branch: any row at all keeps it.
    for model in (Employee, Department, WorkSchedule, User, Camera):
        n = await db.scalar(select(func.count()).select_from(model).where(model.branch_id == b.id))
        if n:
            raise conflict(
                "BRANCH_NOT_EMPTY",
                "The branch still has employees, departments, schedules, users or cameras. "
                "Close it instead (its data is kept).",
            )
    await _audit(db, ctx, request, "BRANCH_DELETED", b, {"name": [b.name, None]})
    await db.delete(b)


@router.get("/overview", response_model=OverviewOut, summary="Attendance of every branch on a date")
async def overview(
    ctx: Manager, db: DbSession, on: Annotated[date, Query(alias="date")]
) -> OverviewOut:
    branches = list(
        (
            await db.execute(select(Branch).order_by(Branch.id != MAIN_BRANCH_ID, Branch.name))
        ).scalars()
    )
    outs = await _outs(db, branches)
    emps = list(
        (
            await db.execute(
                select(Employee).where(
                    Employee.deleted_at.is_(None), Employee.status != EmployeeStatus.TERMINATED
                )
            )
        ).scalars()
    )
    results = await att.compute_range(db, emps, on, on)
    per: dict[uuid.UUID, Counter[str]] = {b.id: Counter() for b in branches}
    for e, r in results:
        c = per.setdefault(e.branch_id, Counter())
        c[r.status.value] += 1
        c["EMPLOYEES"] += 1
        c["IN_PROGRESS"] += int(r.in_progress)
    grand: Counter[str] = Counter()
    for c in per.values():
        grand.update(c)
    return OverviewOut(
        date=on,
        branches=[BranchDay(branch=o, totals=dict(per.get(o.id, {}))) for o in outs],
        totals=dict(grand),
    )
