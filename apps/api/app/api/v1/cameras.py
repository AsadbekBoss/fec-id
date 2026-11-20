"""/api/v1/cameras: camera configuration, status and on-demand checks (API_CONTRACTS §2.6).

* Credentials are write-only and stored encrypted. Responses carry only `has_credentials` and
  a redacted stream URL. The browser never receives an RTSP password.
* `recognition_enabled` can be switched on only after a PASSED quality test. Changing the
  stream (provider, address, path, device, credentials, settings) resets the quality status to
  UNTESTED and switches recognition off again.
* Snapshots show people: viewing one needs cameras:write and is audited. They are not stored.
* Probe, snapshot and quality test are executed by the camera service (local processing).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.errors import AppError, conflict, not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.core.config import get_settings
from app.core.text import escape_like, normalize_search
from app.db import audit
from app.models import Branch, Camera, Department
from app.models.enums import (
    ActorType,
    CameraDirection,
    CameraProvider,
    CameraQualityStatus,
    CameraStatus,
    CameraVendor,
)
from app.schemas.cameras import (
    CameraCreate,
    CameraOut,
    CameraUpdate,
    Monitoring,
    ProbeResult,
    QualityTestRequest,
    validate_provider_fields,
)
from app.schemas.common import Page
from app.services.camera_service import CameraServiceDep, source_spec, stream_url_redacted

router = APIRouter(prefix="/cameras", tags=["cameras"])

Reader = Annotated[AuthContext, Depends(require(Permission.CAMERAS_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.CAMERAS_WRITE))]

# Changing any of these changes the stream: earlier quality results no longer apply.
STREAM_FIELDS = {
    "provider",
    "host",
    "rtsp_port",
    "main_stream_path",
    "sub_stream_path",
    "device",
    "settings",
}
PROVIDER_ONLY = {
    CameraProvider.RTSP: {"host", "rtsp_port", "onvif_port", "main_stream_path", "sub_stream_path"},
    CameraProvider.USB: {"device"},
    CameraProvider.TEST: set(),
}


def to_out(c: Camera) -> CameraOut:
    out = CameraOut.model_validate(c, from_attributes=True)
    out.stream_url_redacted = stream_url_redacted(c)
    if c.is_enabled:
        stale_after = timedelta(seconds=get_settings().camera_status_stale_seconds)
        reported = c.runtime_reported_at
        out.monitoring = Monitoring(
            reported_at=reported,
            stale=reported is None or datetime.now(UTC) - reported > stale_after,
            runtime=c.runtime,
        )
    return out


async def _fresh_out(db: DbSession, c: Camera) -> CameraOut:
    """Flush, then reload server-generated columns (updated_at) before serialising."""
    await db.flush()
    await db.refresh(c)
    return to_out(c)


async def _get(db: DbSession, ctx: AuthContext, camera_id: uuid.UUID) -> Camera:
    c = (
        await db.execute(
            select(Camera).where(
                Camera.id == camera_id, Camera.deleted_at.is_(None), ctx.scope(Camera.branch_id)
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise not_found("Camera")
    return c


async def _flush_or_conflict(db: DbSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise conflict("CAMERA_EXISTS", "A camera with this name already exists.") from None


def _clear_foreign_fields(c: Camera) -> None:
    """Fields of other providers are cleared so the row never carries stale addresses."""
    keep = PROVIDER_ONLY[c.provider]
    for f in ("host", "onvif_port", "main_stream_path", "sub_stream_path", "device"):
        if f not in keep:
            setattr(c, f, None)
    if c.provider is not CameraProvider.RTSP:
        c.clear_credentials()


def _reset_quality(c: Camera) -> None:
    c.quality_status = CameraQualityStatus.UNTESTED
    c.quality_tested_at = None
    c.quality_report = None
    c.recognition_enabled = False


def _require_quality(c: Camera) -> None:
    if c.quality_status is not CameraQualityStatus.PASSED:
        raise AppError(
            409,
            "QUALITY_NOT_PASSED",
            "Conflict",
            "Run a stream quality test that passes before enabling recognition on this camera.",
        )


async def _audit(
    db: DbSession,
    ctx: AuthContext,
    request: Request,
    action: str,
    c: Camera,
    changes: dict[str, Any] | None = None,
) -> None:
    await audit.record(
        db,
        action=action,
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="camera",
        entity_id=c.id,
        ip=client_ip(request),
        changes=changes,
    )


@router.get("", response_model=Page[CameraOut], summary="List cameras")
async def list_cameras(
    ctx: Reader,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    provider: CameraProvider | None = None,
    direction: CameraDirection | None = None,
    status_: Annotated[CameraStatus | None, Query(alias="status")] = None,
    is_enabled: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[CameraOut]:
    stmt = select(Camera).where(Camera.deleted_at.is_(None), ctx.scope(Camera.branch_id))
    if q:
        term = f"%{escape_like(normalize_search(q))}%"
        stmt = stmt.where(
            or_(Camera.name.ilike(term, escape="\\"), Camera.location.ilike(term, escape="\\"))
        )
    if provider:
        stmt = stmt.where(Camera.provider == provider)
    if direction:
        stmt = stmt.where(Camera.direction == direction)
    if status_:
        stmt = stmt.where(Camera.status == status_)
    if is_enabled is not None:
        stmt = stmt.where(Camera.is_enabled == is_enabled)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(Camera.name, Camera.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars()
    return Page(items=[to_out(c) for c in rows], page=page, page_size=page_size, total=total)


@router.post(
    "",
    response_model=CameraOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {}, 422: {}},
    summary="Create camera (DRAFT, disabled, quality UNTESTED)",
)
async def create_camera(
    body: CameraCreate, ctx: Writer, request: Request, db: DbSession
) -> CameraOut:
    if body.recognition_enabled:
        raise AppError(
            409,
            "QUALITY_NOT_PASSED",
            "Conflict",
            "Recognition can be enabled only after a passed stream quality test.",
        )
    data = body.model_dump(
        exclude={
            "username",
            "password",
            "settings",
            "recognition_enabled",
            "roi",
            "department_ids",
            "branch_id",
        },
        exclude_none=True,
    )
    branch = body.branch_id or ctx.write_branch
    await _check_branch(db, ctx, branch)
    data["branch_id"] = branch
    data["department_ids"] = await _check_departments(db, branch, body.department_ids or [])
    c = Camera(
        **data,
        settings=(body.settings.model_dump() if body.settings else {}),
        roi=[list(p) for p in body.roi] if body.roi else None,
        recognition_enabled=False,
        is_enabled=False,
        status=CameraStatus.DRAFT,
    )
    if body.username is not None and body.password is not None:
        c.set_credentials(body.username, body.password)
    _clear_foreign_fields(c)
    db.add(c)
    await _flush_or_conflict(db)
    await _audit(
        db,
        ctx,
        request,
        "CAMERA_CREATED",
        c,
        {
            "name": [None, c.name],
            "provider": [None, c.provider.value],
            "direction": [None, c.direction.value],
            "credentials_set": [None, c.has_credentials],
        },
    )
    return await _fresh_out(db, c)


@router.get("/{camera_id}", response_model=CameraOut, responses={404: {}}, summary="Get camera")
async def get_camera(camera_id: uuid.UUID, ctx: Reader, db: DbSession) -> CameraOut:
    return to_out(await _get(db, ctx, camera_id))


@router.patch(
    "/{camera_id}",
    response_model=CameraOut,
    responses={404: {}, 409: {}, 422: {}},
    summary="Update camera (bumps config_version)",
)
async def update_camera(
    camera_id: uuid.UUID, body: CameraUpdate, ctx: Writer, request: Request, db: DbSession
) -> CameraOut:
    c = await _get(db, ctx, camera_id)
    changes = body.model_dump(
        exclude_unset=True,
        exclude={"username", "password", "clear_credentials", "department_ids", "branch_id"},
    )
    if "settings" in changes and body.settings is not None:
        changes["settings"] = body.settings.model_dump()
    if changes.get("roi") is not None:
        changes["roi"] = [list(p) for p in changes["roi"]]
    for required in ("name", "provider", "vendor", "direction", "rtsp_port", "recognition_enabled"):
        if required in changes and changes[required] is None:
            raise validation_error(required, "required", f"{required} cannot be null")
    if "settings" in changes and changes["settings"] is None:
        changes["settings"] = {}

    provider = changes.get("provider", c.provider)
    merged = {k: changes.get(k, getattr(c, k)) for k in ("host", "main_stream_path", "device")}
    try:
        validate_provider_fields(
            provider,
            merged["host"],
            merged["main_stream_path"],
            merged["device"],
            body.username,
            body.password,
            has_stored_credentials=c.has_credentials
            and not body.clear_credentials
            and provider is CameraProvider.RTSP,
        )
    except ValueError as e:
        raise validation_error("provider", "invalid", str(e)) from None

    diff: dict[str, list[Any]] = {}
    for k, v in changes.items():
        if k == "recognition_enabled":
            continue
        old = getattr(c, k)
        if old != v:
            diff[k] = [_fmt(old), _fmt(v)]
            setattr(c, k, v)
    had_credentials = c.has_credentials
    if body.clear_credentials:
        c.clear_credentials()
    if body.username is not None and body.password is not None:
        c.set_credentials(body.username, body.password)
    _clear_foreign_fields(c)  # also drops credentials when the provider is no longer RTSP
    if body.username is not None:
        diff["credentials"] = ["set" if had_credentials else "none", "replaced"]
    elif had_credentials and not c.has_credentials:
        diff["credentials"] = ["set", "cleared"]

    stream_changed = bool(STREAM_FIELDS & diff.keys()) or "credentials" in diff
    if stream_changed and c.quality_status is not CameraQualityStatus.UNTESTED:
        _reset_quality(c)
        diff["quality_status"] = ["reset", CameraQualityStatus.UNTESTED.value]
    if "recognition_enabled" in changes and changes["recognition_enabled"] != c.recognition_enabled:
        if changes["recognition_enabled"]:
            _require_quality(c)
        diff["recognition_enabled"] = [c.recognition_enabled, changes["recognition_enabled"]]
        c.recognition_enabled = changes["recognition_enabled"]
    # Departments only change how the Core API counts events: no camera reconnect needed.
    dept_diff: dict[str, list[Any]] = {}
    branch = c.branch_id
    if body.branch_id is not None and body.branch_id != c.branch_id:
        await _check_branch(db, ctx, body.branch_id)
        dept_diff["branch_id"] = [str(c.branch_id), str(body.branch_id)]
        branch = c.branch_id = body.branch_id
        if body.department_ids is None:
            body.department_ids = list(c.department_ids or [])
    if body.department_ids is not None:
        new_depts = await _check_departments(db, branch, body.department_ids)
        if set(new_depts) != set(c.department_ids or []):
            dept_diff["department_ids"] = [
                [str(d) for d in c.department_ids or []],
                [str(d) for d in new_depts],
            ]
            c.department_ids = new_depts
    if diff:
        c.config_version += 1
        if c.is_enabled and stream_changed:
            c.status = CameraStatus.OFFLINE  # the camera service reconnects with the new config
            c.runtime, c.runtime_reported_at = None, None
    await _flush_or_conflict(db)
    if diff or dept_diff:
        await _audit(db, ctx, request, "CAMERA_UPDATED", c, {**diff, **dept_diff})
    return await _fresh_out(db, c)


async def _check_branch(db: DbSession, ctx: AuthContext, branch_id: uuid.UUID) -> None:
    if not ctx.sees(branch_id) or await db.get(Branch, branch_id) is None:
        raise validation_error("branch_id", "not_found", "Branch not found.")


async def _check_departments(
    db: DbSession, branch_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Existing, non-deleted departments of the camera's branch; duplicates removed."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        return []
    found = set(
        (
            await db.execute(
                select(Department.id).where(
                    Department.id.in_(unique),
                    Department.deleted_at.is_(None),
                    Department.branch_id == branch_id,
                )
            )
        ).scalars()
    )
    missing = [str(d) for d in unique if d not in found]
    if missing:
        raise validation_error("department_ids", "not_found", f"Unknown departments: {missing}")
    return unique


def _fmt(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float | str):
        return v.value if isinstance(v, CameraProvider | CameraDirection | CameraVendor) else v
    return str(v) if not isinstance(v, dict | list) else "changed"


@router.delete(
    "/{camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}},
    summary="Delete (soft); the camera service stops its worker",
)
async def delete_camera(camera_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession) -> None:
    c = await _get(db, ctx, camera_id)
    c.deleted_at = datetime.now(UTC)
    c.is_enabled = False
    c.status = CameraStatus.DISABLED
    c.config_version += 1
    await _audit(db, ctx, request, "CAMERA_DELETED", c)


@router.post(
    "/{camera_id}/enable",
    response_model=CameraOut,
    responses={404: {}},
    summary="Enable: the camera service starts monitoring it",
)
async def enable_camera(
    camera_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession
) -> CameraOut:
    c = await _get(db, ctx, camera_id)
    if not c.is_enabled:
        c.is_enabled = True
        c.status = CameraStatus.OFFLINE  # until the camera service reports a frame
        c.runtime, c.runtime_reported_at = None, None
        c.config_version += 1
        await _audit(db, ctx, request, "CAMERA_ENABLED", c)
    return await _fresh_out(db, c)


@router.post(
    "/{camera_id}/disable",
    response_model=CameraOut,
    responses={404: {}},
    summary="Disable: monitoring stops, the stream is released",
)
async def disable_camera(
    camera_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession
) -> CameraOut:
    c = await _get(db, ctx, camera_id)
    if c.is_enabled:
        c.is_enabled = False
        c.status = CameraStatus.DISABLED
        c.config_version += 1
        await _audit(db, ctx, request, "CAMERA_DISABLED", c)
    return await _fresh_out(db, c)


@router.post(
    "/{camera_id}/test",
    response_model=ProbeResult,
    responses={404: {}, 503: {}},
    summary="Connect and read one frame (connection check, not a quality check)",
)
async def test_camera(
    camera_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession, svc: CameraServiceDep
) -> ProbeResult:
    c = await _get(db, ctx, camera_id)
    result = ProbeResult.model_validate(await svc.probe(source_spec(c)))
    await _audit(
        db,
        ctx,
        request,
        "CAMERA_TESTED",
        c,
        {"ok": [None, result.ok], "error_code": [None, result.error_code]},
    )
    return result


@router.get(
    "/{camera_id}/snapshot",
    responses={200: {"content": {"image/jpeg": {}}}, 404: {}, 502: {}, 503: {}},
    summary="Current frame as JPEG (not stored; audited)",
)
async def camera_snapshot(
    camera_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession, svc: CameraServiceDep
) -> Response:
    c = await _get(db, ctx, camera_id)
    jpeg = await svc.snapshot(source_spec(c))
    await _audit(db, ctx, request, "CAMERA_SNAPSHOT_VIEWED", c)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post(
    "/{camera_id}/quality-test",
    response_model=CameraOut,
    responses={404: {}, 503: {}},
    summary="Sample the stream and assess whether it is suitable for face recognition",
)
async def quality_test(
    camera_id: uuid.UUID,
    ctx: Writer,
    request: Request,
    db: DbSession,
    svc: CameraServiceDep,
    body: QualityTestRequest | None = None,
) -> CameraOut:
    c = await _get(db, ctx, camera_id)
    duration = (body or QualityTestRequest()).duration_seconds
    report = await svc.quality_test(source_spec(c), duration)
    try:
        verdict = CameraQualityStatus(str(report.get("verdict")))
        if verdict is CameraQualityStatus.UNTESTED:
            raise ValueError
    except ValueError:
        raise AppError(
            502,
            "BAD_QUALITY_REPORT",
            "Bad gateway",
            "The camera service returned an invalid quality report.",
        ) from None
    old = c.quality_status
    c.quality_status, c.quality_tested_at = verdict, datetime.now(UTC)
    c.quality_report = report
    if verdict is not CameraQualityStatus.PASSED and c.recognition_enabled:
        c.recognition_enabled = False
        c.config_version += 1
    await _audit(
        db, ctx, request, "CAMERA_QUALITY_TESTED", c, {"quality_status": [old.value, verdict.value]}
    )
    return await _fresh_out(db, c)
