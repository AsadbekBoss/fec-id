"""/internal/v1: endpoints for the camera service (API_CONTRACTS §4.1). Internal network only:
the production reverse proxy must not route /internal (ARCHITECTURE §7.3).

Auth: `Authorization: Bearer <CAMERA_SERVICE_TOKEN>` (constant-time compare). The token is
not configured by default, in which case every request is rejected.
"""

from __future__ import annotations

import base64
import hmac
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from pydantic import Field, SecretStr
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import BEARER_CHALLENGE, AppError
from app.attendance import service as attendance_svc
from app.core import crypto, storage, uploads
from app.core.config import get_settings
from app.db import audit
from app.models import (
    AttendanceEvent,
    BiometricConsent,
    Camera,
    Employee,
    FaceEmbedding,
    SystemSetting,
)
from app.models.enums import (
    ActorType,
    AttendanceMethod,
    CameraQualityStatus,
    CameraStatus,
    EmployeeStatus,
    EventSource,
    RecognitionDecision,
)
from app.schemas.cameras import StatusBatch
from app.schemas.common import StrictModel
from app.services import reviews
from app.services.camera_service import source_spec

SERVICE = "camera-service"
RECOGNITION = "recognition-service"


def _check_bearer(token: SecretStr | None, authorization: str | None) -> None:
    scheme, _, presented = (authorization or "").partition(" ")
    if (
        token is None
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(presented.encode(), token.get_secret_value().encode())
    ):
        raise AppError(
            401,
            "NOT_AUTHENTICATED",
            "Unauthorized",
            "Invalid service token.",
            headers=BEARER_CHALLENGE,
        )


def require_camera_service(authorization: Annotated[str | None, Header()] = None) -> str:
    _check_bearer(get_settings().camera_service_token, authorization)
    return SERVICE


def require_recognition_service(authorization: Annotated[str | None, Header()] = None) -> str:
    """RECOGNITION_GALLERY_TOKEN: distinct from every token the camera service holds, so the
    camera service can never read biometric templates."""
    _check_bearer(get_settings().recognition_gallery_token, authorization)
    return RECOGNITION


Service = Annotated[str, Depends(require_camera_service)]
RecognitionService = Annotated[str, Depends(require_recognition_service)]
router = APIRouter(prefix="/internal/v1", tags=["internal"], include_in_schema=False)

STATE_TO_STATUS = {
    "ONLINE": CameraStatus.ACTIVE,
    "DEGRADED": CameraStatus.DEGRADED,
    "CONNECTING": CameraStatus.OFFLINE,
    "OFFLINE": CameraStatus.OFFLINE,
}


@router.get("/cameras")
async def enabled_cameras(_: Service, db: DbSession, response: Response) -> dict[str, Any]:
    """Enabled cameras with DECRYPTED credentials, for the camera service only. Never logged."""
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await db.execute(
            select(Camera)
            .where(Camera.deleted_at.is_(None), Camera.is_enabled.is_(True))
            .order_by(Camera.id)
        )
    ).scalars()
    return {"cameras": [source_spec(c) for c in rows]}


@router.post("/cameras/status", status_code=204)
async def report_status(body: StatusBatch, service: Service, db: DbSession) -> None:
    """Heartbeat from the camera service. Reports for disabled/deleted cameras or for an older
    config_version are ignored (the service has not picked up the new config yet)."""
    now = datetime.now(UTC)
    ids = [r.camera_id for r in body.reports]
    cams = (
        {c.id: c for c in (await db.execute(select(Camera).where(Camera.id.in_(ids)))).scalars()}
        if ids
        else {}
    )
    for r in body.reports:
        c = cams.get(r.camera_id)
        if c is None or c.deleted_at is not None or not c.is_enabled:
            continue
        if r.config_version != c.config_version or r.state == "STOPPED":
            continue
        new_status = STATE_TO_STATUS[r.state]
        if new_status is not c.status:
            await audit.record(
                db,
                action="CAMERA_STATUS_CHANGED",
                actor_type=ActorType.SERVICE,
                actor_service=service,
                entity_type="camera",
                entity_id=c.id,
                changes={
                    "status": [c.status.value, new_status.value],
                    "error_code": [None, r.error_code],
                },
            )
            c.status = new_status
        if r.last_frame_at:
            c.last_seen_at = r.last_frame_at
        c.last_error = (r.last_error or None) and r.last_error[:500]
        c.runtime = r.model_dump(mode="json", exclude={"camera_id", "config_version"})
        c.runtime_reported_at = now


@router.get("/gallery")
async def gallery(
    _: RecognitionService,
    db: DbSession,
    response: Response,
    model_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, Any]:
    """ENCRYPTED templates of employees who may be recognised: active, FACE method, with an
    active and documented consent. The recognition service decrypts them in memory."""
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await db.execute(
            select(FaceEmbedding)
            .join(Employee, Employee.id == FaceEmbedding.employee_id)
            .join(BiometricConsent, BiometricConsent.id == FaceEmbedding.consent_id)
            .where(
                FaceEmbedding.is_active.is_(True),
                FaceEmbedding.model_id == model_id,
                Employee.deleted_at.is_(None),
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.attendance_method == AttendanceMethod.FACE,
                BiometricConsent.revoked_at.is_(None),
                BiometricConsent.signed_document_key.is_not(None),
            )
            .order_by(FaceEmbedding.id)
        )
    ).scalars()
    templates = [
        {
            "template_id": str(f.id),
            "employee_id": str(f.employee_id),
            "model_id": f.model_id,
            "dim": f.dim,
            "ciphertext": base64.b64encode(f.embedding_enc).decode(),
        }
        for f in rows
    ]
    return {"templates": templates, "count": len(templates)}


# --- recognition -> attendance (camera service) -----------------------------------------------


class RecognitionEventIn(StrictModel):
    event_uid: uuid.UUID
    camera_id: uuid.UUID
    employee_id: uuid.UUID
    event_time: datetime
    decision: Literal["MATCHED"]  # only confident identifications are ever sent
    model_id: str = Field(min_length=1, max_length=64)
    similarity: float | None = Field(default=None, ge=-1, le=1)
    second_similarity: float | None = Field(default=None, ge=-1, le=1)
    quality_score: float | None = Field(default=None, ge=0, le=1)


@router.post("/recognition-events")
async def recognition_event(body: RecognitionEventIn, _: Service, db: DbSession) -> dict[str, Any]:
    """A confident recognition from a live camera. Becomes an attendance event ONLY when the
    master switch `recognition.attendance_enabled` is on and the camera passed its quality
    test with recognition enabled. All attendance rules (debounce, IN/OUT sequence, inactive
    employees, manual-method employees) are applied by the attendance engine."""
    enabled = await db.get(SystemSetting, "recognition.attendance_enabled")
    if enabled is None or enabled.value is not True:
        return {"recorded": False, "reason": "attendance_disabled"}
    cam = await db.get(Camera, body.camera_id)
    if (
        cam is None
        or cam.deleted_at is not None
        or not cam.is_enabled
        or not cam.recognition_enabled
        or cam.quality_status is not CameraQualityStatus.PASSED
    ):
        return {"recorded": False, "reason": "camera_not_enabled"}
    try:
        res = await attendance_svc.ingest_camera_event(
            db,
            event_uid=body.event_uid,
            camera_id=body.camera_id,
            event_time=body.event_time,
            decision=RecognitionDecision.MATCHED,
            employee_id=body.employee_id,
            model_id=body.model_id,
            similarity=body.similarity,
            second_similarity=body.second_similarity,
            quality_score=body.quality_score,
        )
    except attendance_svc.EventRejected as e:
        raise AppError(
            422,
            "EVENT_REJECTED",
            "Event rejected",
            e.message,
            errors=[{"field": e.field, "code": e.code, "message": e.message}],
        ) from None
    ev = res.event
    return {
        "recorded": True,
        "created": res.created,
        "counted": ev.counts_for_attendance,
        "direction": ev.direction.value if ev.direction else None,
        "ignore_reason": ev.ignore_reason,
        "work_date": ev.work_date.isoformat() if ev.work_date else None,
    }


# --- review queue: uncertain recognitions with a face crop (camera service) -----------------


class ReviewMeta(StrictModel):
    event_uid: uuid.UUID
    camera_id: uuid.UUID
    event_time: datetime
    decision: Literal["AMBIGUOUS", "UNKNOWN"]
    model_id: str = Field(min_length=1, max_length=64)
    similarity: float | None = Field(default=None, ge=-1, le=1)
    second_similarity: float | None = Field(default=None, ge=-1, le=1)
    quality_score: float | None = Field(default=None, ge=0, le=1)


@router.post("/recognition-reviews")
async def recognition_review(
    _: Service,
    db: DbSession,
    meta: Annotated[str, Form(max_length=4000)],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Store an uncertain observation for a human decision. Gated like attendance: master switch,
    review switch, camera passed its quality test; UNKNOWN faces only if allowed by settings."""
    try:
        m = ReviewMeta.model_validate_json(meta)
    except ValueError:
        raise AppError(422, "INVALID_META", "Invalid", "meta must be valid JSON.") from None
    if await reviews.setting(db, "recognition.attendance_enabled", False) is not True:
        return {"stored": False, "reason": "attendance_disabled"}
    if await reviews.setting(db, "recognition.review_enabled", True) is not True:
        return {"stored": False, "reason": "review_disabled"}
    if m.decision == "UNKNOWN" and (
        await reviews.setting(db, "recognition.store_unknown_snapshots", False) is not True
    ):
        return {"stored": False, "reason": "unknown_not_stored"}
    cam = await db.get(Camera, m.camera_id)
    if (
        cam is None
        or cam.deleted_at is not None
        or not cam.is_enabled
        or not cam.recognition_enabled
        or cam.quality_status is not CameraQualityStatus.PASSED
    ):
        return {"stored": False, "reason": "camera_not_enabled"}
    limits = await attendance_svc.load_settings(db)
    now = datetime.now(UTC)
    if m.event_time.tzinfo is None or m.event_time > now + limits.max_clock_skew:
        raise AppError(422, "EVENT_REJECTED", "Event rejected", "event_time is in the future.")
    if m.event_time < now - limits.max_event_age:
        raise AppError(422, "EVENT_REJECTED", "Event rejected", "event_time is too old.")
    if (
        await db.execute(select(AttendanceEvent.id).where(AttendanceEvent.event_uid == m.event_uid))
    ).first():
        return {"stored": False, "reason": "duplicate"}
    try:
        clean = await uploads.clean_photo(await file.read(512 * 1024 + 1), 512 * 1024)
    except uploads.UploadRejected as e:
        raise AppError(422, "INVALID_FILE", "Invalid file", e.message) from None
    await reviews.purge_expired(db, datetime.now(UTC))
    key = storage.new_key("review_snapshots", cam.id)
    await storage.put(key, clean.data, crypto.Purpose.SNAPSHOT)
    ev = AttendanceEvent(
        event_uid=m.event_uid,
        event_time=m.event_time,
        source=EventSource.CAMERA,
        camera_id=cam.id,
        decision=RecognitionDecision(m.decision),
        model_id=m.model_id,
        similarity=m.similarity,
        second_similarity=m.second_similarity,
        quality_score=m.quality_score,
        counts_for_attendance=False,
        ignore_reason="UNMATCHED",
        snapshot_key=key,
        review_status="PENDING",
    )
    db.add(ev)
    try:
        await db.flush()
    except Exception:
        await storage.delete(key)
        raise
    return {"stored": True, "review_id": ev.id}
