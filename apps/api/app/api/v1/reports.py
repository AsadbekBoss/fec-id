"""/api/v1/reports: monthly timesheet, lateness and absence (API_CONTRACTS §2.10).

`format=json` for the on-screen preview, `xlsx` (Excel) or `pdf` for download. Downloads are
audited (a report is a bulk export of personal data). Numbers come from the attendance engine,
computed live, so they always match the Attendance pages.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import not_found, validation_error
from app.api.v1.attendance import _employees
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import Branch, Department
from app.models.enums import ActorType
from app.services import reports as rpt

router = APIRouter(prefix="/reports", tags=["reports"])
Reader = Annotated[AuthContext, Depends(require(Permission.REPORTS_READ))]
Format = Literal["json", "xlsx", "pdf"]
MAX_RANGE_DAYS = 92
MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


async def _dept(db: DbSession, ctx: AuthContext, department_id: uuid.UUID | None) -> str | None:
    """Scope label of the report: the department, else the selected branch, else None (all)."""
    if department_id is None:
        if ctx.branch_id is None:
            return None
        b = await db.get(Branch, ctx.branch_id)
        return f"Filial: {b.name}" if b else None
    d = (
        await db.execute(
            select(Department).where(
                Department.id == department_id,
                Department.deleted_at.is_(None),
                ctx.scope(Department.branch_id),
            )
        )
    ).scalar_one_or_none()
    if d is None:
        raise not_found("Department")
    return d.name


def _check_range(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise validation_error("date_to", "before_start", "date_to must not be before date_from.")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise validation_error(
            "date_to", "range_too_long", f"At most {MAX_RANGE_DAYS} days per report."
        )


async def _respond(
    rep: rpt.Report,
    fmt: Format,
    filename: str,
    ctx: AuthContext,
    request: Request,
    db: DbSession,
    params: dict[str, Any],
) -> Response | dict[str, Any]:
    if fmt == "json":
        return rep.as_json()
    render = rpt.to_xlsx if fmt == "xlsx" else rpt.to_pdf
    data = await anyio.to_thread.run_sync(render, rep)
    await audit.record(
        db,
        action="REPORT_EXPORTED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="report",
        entity_id=rep.kind,
        ip=client_ip(request),
        changes={
            "format": [None, fmt],
            "rows": [None, len(rep.rows)],
            **{k: [None, v] for k, v in params.items()},
        },
    )
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return Response(
        data,
        media_type=MEDIA[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{safe}.{fmt}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/timesheet", summary="Monthly timesheet (employee x day)", response_model=None)
async def timesheet(
    ctx: Reader,
    request: Request,
    db: DbSession,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")],
    department_id: uuid.UUID | None = None,
    format: Format = "json",
) -> Response | dict[str, Any]:
    first = date.fromisoformat(month + "-01")
    dept_name = await _dept(db, ctx, department_id)
    emps = await _employees(db, ctx, None, department_id)
    rep = await rpt.timesheet(db, emps, first, dept_name)
    return await _respond(
        rep,
        format,
        f"tabel_{month}",
        ctx,
        request,
        db,
        {"month": month, "department_id": str(department_id or "")},
    )


@router.get("/lateness", summary="Late arrivals in a date range", response_model=None)
async def lateness(
    ctx: Reader,
    request: Request,
    db: DbSession,
    date_from: date,
    date_to: date,
    department_id: uuid.UUID | None = None,
    format: Format = "json",
) -> Response | dict[str, Any]:
    _check_range(date_from, date_to)
    dept_name = await _dept(db, ctx, department_id)
    emps = await _employees(db, ctx, None, department_id)
    rep = await rpt.lateness(db, emps, date_from, date_to, dept_name)
    return await _respond(
        rep,
        format,
        f"kechikishlar_{date_from}_{date_to}",
        ctx,
        request,
        db,
        {"date_from": str(date_from), "date_to": str(date_to)},
    )


@router.get("/absence", summary="Absent working days in a date range", response_model=None)
async def absence(
    ctx: Reader,
    request: Request,
    db: DbSession,
    date_from: date,
    date_to: date,
    department_id: uuid.UUID | None = None,
    format: Format = "json",
) -> Response | dict[str, Any]:
    _check_range(date_from, date_to)
    dept_name = await _dept(db, ctx, department_id)
    emps = await _employees(db, ctx, None, department_id)
    rep = await rpt.absence(db, emps, date_from, date_to, dept_name)
    return await _respond(
        rep,
        format,
        f"kelmaganlar_{date_from}_{date_to}",
        ctx,
        request,
        db,
        {"date_from": str(date_from), "date_to": str(date_to)},
    )
