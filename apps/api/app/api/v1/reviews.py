"""/api/v1/recognition/reviews: human decisions on uncertain recognitions (API_CONTRACTS §2.7).

View: `biometrics:read` (shows faces). Decide: `attendance:write`. The face crop is shown only
to reviewers, every view is audited, and the crop is deleted as soon as a decision is made.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, true

from app.api import scope
from app.api.deps import DbSession
from app.api.errors import AppError, conflict, not_found, validation_error
from app.attendance import engine
from app.attendance import service as att
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.core import crypto, storage
from app.db import audit
from app.models import AttendanceEvent, Camera, Employee
from app.models.enums import ActorType, CameraDirection
from app.schemas.common import Page, StrictModel
from app.services import reviews

router = APIRouter(prefix="/recognition/reviews", tags=["recognition"])
Viewer = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_READ))]
Decider = Annotated[
    AuthContext, Depends(require(Permission.BIOMETRICS_READ, Permission.ATTENDANCE_WRITE))
]


class PersonRef(BaseModel):
    id: uuid.UUID
    employee_code: str
    full_name: str


class ReviewOut(BaseModel):
    id: int
    event_time: datetime
    camera_id: uuid.UUID | None
    camera_name: str | None
    camera_direction: CameraDirection | None
    decision: str
    similarity: float | None
    has_snapshot: bool
    review_status: str
    reviewed_at: datetime | None
    reviewed_by_user_id: uuid.UUID | None
    employee: PersonRef | None = None  # set after ASSIGNED
    punch_event_id: int | None = None


class AssignIn(StrictModel):
    employee_id: uuid.UUID
    direction: Literal["IN", "OUT"] | None = None  # required for BOTH / NONE cameras
    note: str | None = Field(default=None, max_length=500)


class DismissIn(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


def _person(e: Employee | None) -> PersonRef | None:
    if e is None:
        return None
    return PersonRef(
        id=e.id, employee_code=e.employee_code, full_name=f"{e.last_name} {e.first_name}"
    )


async def _out(db: DbSession, ev: AttendanceEvent) -> ReviewOut:
    cam = await db.get(Camera, ev.camera_id) if ev.camera_id else None
    emp = await db.get(Employee, ev.review_employee_id) if ev.review_employee_id else None
    return ReviewOut(
        id=ev.id,
        event_time=ev.event_time,
        camera_id=ev.camera_id,
        camera_name=cam.name if cam else None,
        camera_direction=cam.direction if cam else None,
        decision=ev.decision.value if ev.decision else "",
        similarity=ev.similarity,
        has_snapshot=ev.snapshot_key is not None,
        review_status=ev.review_status or "",
        reviewed_at=ev.reviewed_at,
        reviewed_by_user_id=ev.reviewed_by_user_id,
        employee=_person(emp),
        punch_event_id=ev.review_event_id,
    )


def _in_scope(ctx: AuthContext) -> Any:
    """Reviews belong to the branch of the camera that saw the face."""
    return true() if ctx.branch_id is None else AttendanceEvent.camera_id.in_(scope.camera_ids(ctx))


async def _get(
    db: DbSession, ctx: AuthContext, review_id: int, *, lock: bool = False
) -> AttendanceEvent:
    stmt = select(AttendanceEvent).where(
        AttendanceEvent.id == review_id,
        AttendanceEvent.review_status.is_not(None),
        _in_scope(ctx),
    )
    ev = (await db.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if ev is None:
        raise not_found("Review")
    return ev


@router.get("", response_model=Page[ReviewOut], summary="Review queue (newest first)")
async def list_reviews(
    ctx: Viewer,
    db: DbSession,
    status: Literal["PENDING", "ASSIGNED", "DISMISSED", "ALL"] = "PENDING",
    camera_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 24,
) -> Page[ReviewOut]:
    await reviews.purge_expired(db, datetime.now(UTC))
    stmt = select(AttendanceEvent).where(AttendanceEvent.review_status.is_not(None), _in_scope(ctx))
    if status != "ALL":
        stmt = stmt.where(AttendanceEvent.review_status == status)
    if camera_id:
        stmt = stmt.where(AttendanceEvent.camera_id == camera_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(AttendanceEvent.event_time.desc(), AttendanceEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    return Page(
        items=[await _out(db, ev) for ev in rows], page=page, page_size=page_size, total=total
    )


@router.get(
    "/{review_id}/snapshot",
    responses={200: {"content": {"image/jpeg": {}}}, 404: {}, 410: {"description": "Deleted"}},
    summary="Face crop of a review (audited)",
)
async def review_snapshot(review_id: int, ctx: Viewer, request: Request, db: DbSession) -> Response:
    ev = await _get(db, ctx, review_id)
    if ev.snapshot_key is None:
        raise AppError(410, "SNAPSHOT_DELETED", "Gone", "The face crop was deleted.")
    try:
        data = await storage.get(ev.snapshot_key, crypto.Purpose.SNAPSHOT)
    except storage.StorageError:
        raise AppError(410, "SNAPSHOT_DELETED", "Gone", "The face crop was deleted.") from None
    await audit.record(
        db,
        action="REVIEW_SNAPSHOT_VIEWED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="attendance_event",
        entity_id=ev.id,
        ip=client_ip(request),
    )
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


async def _close(ev: AttendanceEvent, status: str, user_id: uuid.UUID) -> None:
    await storage.delete(ev.snapshot_key)
    ev.snapshot_key = None
    ev.review_status = status
    ev.reviewed_at = datetime.now(UTC)
    ev.reviewed_by_user_id = user_id


@router.post(
    "/{review_id}/assign",
    response_model=ReviewOut,
    responses={404: {}, 409: {}, 422: {}},
    summary="This was employee X: add a MANUAL punch (corrections flow). Does not enrol the face.",
)
async def assign(
    review_id: int, body: AssignIn, ctx: Decider, request: Request, db: DbSession
) -> ReviewOut:
    ev = await _get(db, ctx, review_id, lock=True)
    if ev.review_status != "PENDING":
        raise conflict("REVIEW_CLOSED", "This review was already decided.")
    cam = await db.get(Camera, ev.camera_id) if ev.camera_id else None
    direction: str | None = body.direction
    if direction is None:
        if cam is None or cam.direction not in (CameraDirection.IN, CameraDirection.OUT):
            raise validation_error(
                "direction", "required", "Choose IN or OUT: this camera has no fixed direction."
            )
        direction = cam.direction.value
    emp = (
        await db.execute(
            select(Employee).where(
                Employee.id == body.employee_id,
                Employee.deleted_at.is_(None),
                ctx.scope(Employee.branch_id),
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        raise not_found("Employee")
    settings = await att.load_settings(db)
    resolver = await att.ScheduleResolver.load(db)
    work_date = engine.assign_work_date(
        ev.event_time, resolver.for_employee(emp), settings.default_tz
    )
    reason = "Koʻrib chiqish: noaniq tanish"  # noqa: RUF001 (Uzbek letter) + (f" — {body.note}" if body.note else "")
    try:
        result = await att.apply_correction(
            db,
            employee_id=emp.id,
            work_date=work_date,
            reason=reason,
            operations=[att.AddOp(engine.Direction(direction), ev.event_time, reason)],
            user_id=ctx.user_id,
        )
    except att.CorrectionRejected as e:
        if e.status == 409:
            raise AppError(409, e.code, "Conflict", e.message) from None
        raise validation_error(e.field, e.code, e.message) from None
    punch = result.added[0]
    await _close(ev, "ASSIGNED", ctx.user_id)
    ev.review_employee_id = emp.id
    ev.review_event_id = punch.id
    await db.flush()
    await audit.record(
        db,
        action="REVIEW_ASSIGNED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="attendance_event",
        entity_id=ev.id,
        ip=client_ip(request),
        changes={
            "employee_id": [None, str(emp.id)],
            "direction": [None, direction],
            "punch_event_id": [None, punch.id],
            "correction_id": [None, str(result.correction_id)],
        },
    )
    return await _out(db, ev)


@router.post(
    "/{review_id}/dismiss",
    response_model=ReviewOut,
    responses={404: {}, 409: {}},
    summary="Not an employee / not relevant: close the review, delete the crop",
)
async def dismiss(
    review_id: int, ctx: Decider, request: Request, db: DbSession, body: DismissIn | None = None
) -> ReviewOut:
    ev = await _get(db, ctx, review_id, lock=True)
    if ev.review_status != "PENDING":
        raise conflict("REVIEW_CLOSED", "This review was already decided.")
    await _close(ev, "DISMISSED", ctx.user_id)
    await db.flush()
    await audit.record(
        db,
        action="REVIEW_DISMISSED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="attendance_event",
        entity_id=ev.id,
        ip=client_ip(request),
        changes={"reason": [None, (body.reason if body else None)]},
    )
    return await _out(db, ev)
