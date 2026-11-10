"""/internal/v1: endpoints for the camera service (API_CONTRACTS §4.1). Internal network only:
the production reverse proxy must not route /internal (ARCHITECTURE §7.3).

Auth: `Authorization: Bearer <CAMERA_SERVICE_TOKEN>` (constant-time compare). The token is
not configured by default, in which case every request is rejected.
"""

from __future__ import annotations

import base64
import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import SecretStr
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import BEARER_CHALLENGE, AppError
from app.core.config import get_settings
from app.db import audit
from app.models import BiometricConsent, Camera, Employee, FaceEmbedding
from app.models.enums import ActorType, AttendanceMethod, CameraStatus, EmployeeStatus
from app.schemas.cameras import StatusBatch
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
