"""Face enrolment (API_CONTRACTS §2.5) and the live recognition view (§2.7, observation only).

Enrolment rules:
* Only employees with attendance method FACE, not terminated, with an ACTIVE and DOCUMENTED
  consent (also enforced by a DB trigger). Every template records its consent.
* The image is sanitised, sent to the recognition service, and DISCARDED. Only the encrypted
  embedding is stored. Exactly one good-quality face is required.
* A face that already matches ANOTHER enrolled employee is refused (duplicate check).
* At most MAX_FACE_TEMPLATES per employee.

The live view shows what the recognition service sees on cameras with recognition enabled.
Nothing in this module creates attendance events: identification and attendance stay separate
until the evaluation report is accepted (DEVELOPMENT_PROGRESS D22).
"""

from __future__ import annotations

import base64
import struct
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.api.errors import AppError, conflict, not_found
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.core import uploads
from app.core.config import get_settings
from app.db import audit
from app.models import Camera, Employee, FaceEmbedding
from app.models.enums import ActorType, AttendanceMethod, EmployeeStatus
from app.schemas.common import StrictModel
from app.services import employees as emp_svc
from app.services.camera_service import CameraServiceDep, source_spec
from app.services.recognition_service import RecognitionServiceDep

router = APIRouter(tags=["biometrics"])

BioReader = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_READ))]
BioWriter = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_WRITE))]

MAX_IMAGE_BYTES = 8 * 1024 * 1024

FACE_REASONS = {
    "no_face": "No face was found in the image.",
    "multiple_faces": "More than one face is visible. Use an image with only this person.",
    "low_quality": "The face does not meet the quality requirements.",
    "invalid_image": "The image could not be decoded.",
}


class FaceTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_id: str
    dim: int
    quality_score: float
    created_at: datetime
    created_by_user_id: uuid.UUID


class FromCamera(StrictModel):
    camera_id: uuid.UUID


async def _employee(db: DbSession, employee_id: uuid.UUID) -> Employee:
    e = (
        await db.execute(
            select(Employee).where(Employee.id == employee_id, Employee.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if e is None:
        raise not_found("Employee")
    return e


@router.get(
    "/employees/{employee_id}/face-templates",
    response_model=list[FaceTemplateOut],
    responses={404: {}},
    summary="Face templates of an employee (metadata only, never vectors)",
)
async def list_templates(
    employee_id: uuid.UUID, _: BioReader, db: DbSession
) -> list[FaceTemplateOut]:
    await _employee(db, employee_id)
    rows = (
        await db.execute(
            select(FaceEmbedding)
            .where(FaceEmbedding.employee_id == employee_id, FaceEmbedding.is_active.is_(True))
            .order_by(FaceEmbedding.created_at)
        )
    ).scalars()
    return [FaceTemplateOut.model_validate(f) for f in rows]


async def _enroll(
    db: DbSession,
    ctx: AuthContext,
    request: Request,
    employee_id: uuid.UUID,
    image: bytes,
    rec: RecognitionServiceDep,
    source: str,
) -> FaceTemplateOut:
    emp = await _employee(db, employee_id)
    if emp.attendance_method is not AttendanceMethod.FACE:
        raise conflict(
            "NOT_FACE_METHOD", "This employee uses manual attendance (no biometric processing)."
        )
    if emp.status is EmployeeStatus.TERMINATED:
        raise conflict("EMPLOYEE_TERMINATED", "Terminated employees cannot be enrolled.")
    consent = await emp_svc.active_consent(db, emp.id)  # row lock: serialises enrolments
    if consent is None or consent.signed_document_key is None:
        raise conflict(
            "CONSENT_REQUIRED",
            "Record the employee's biometric consent and upload the signed form first.",
        )
    count = (
        await db.execute(
            select(func.count())
            .select_from(FaceEmbedding)
            .where(FaceEmbedding.employee_id == emp.id, FaceEmbedding.is_active.is_(True))
        )
    ).scalar_one()
    limit = get_settings().max_face_templates
    if count >= limit:
        raise conflict("TEMPLATE_LIMIT", f"At most {limit} face templates per employee.")

    try:
        clean = await uploads.clean_face_image(image, MAX_IMAGE_BYTES)
    except uploads.UploadRejected as e:
        code = 413 if e.code == "too_large" else 422
        raise AppError(code, "INVALID_FILE", "Invalid file", e.message) from None

    result = await rec.extract(clean.data)
    del clean  # the image is not kept
    if not result.get("ok"):
        reason = str(result.get("reason") or "no_face")
        reasons = [str(r) for r in result.get("quality_reasons") or []]
        raise AppError(
            422,
            "FACE_NOT_ACCEPTED",
            "Face not accepted",
            FACE_REASONS.get(reason, "The face could not be used."),
            errors=[{"field": "file", "code": reason, "message": ",".join(reasons) or reason}],
        )
    nearest = result.get("nearest") or None
    if nearest and nearest.get("same_person_likely") and nearest.get("employee_id") != str(emp.id):
        other = await db.get(Employee, uuid.UUID(str(nearest["employee_id"])))
        who = f"{other.employee_code} {other.last_name} {other.first_name}" if other else "another"
        raise conflict(
            "FACE_MATCHES_OTHER_EMPLOYEE",
            f"This face is very similar to an enrolled employee ({who}). Check the identity "
            "before enrolling; one face cannot belong to two employees.",
        )

    raw = base64.b64decode(str(result["embedding_b64"]))
    dim = int(result["dim"])
    if len(raw) != dim * 4:
        raise AppError(502, "BAD_TEMPLATE", "Bad gateway", "Invalid template from the service.")
    vector = list(struct.unpack(f"<{dim}f", raw))
    t = FaceEmbedding(
        employee_id=emp.id,
        consent_id=consent.id,
        model_id=str(result["model_id"]),
        quality_score=max(0.0, min(1.0, float(result.get("quality_score") or 0))),
        created_by_user_id=ctx.user_id,
    )
    t.set_vector(vector)
    del vector, raw
    db.add(t)
    await db.flush()
    await db.refresh(t)
    await audit.record(
        db,
        action="BIOMETRIC_TEMPLATE_ENROLLED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
        changes={
            "template_id": [None, str(t.id)],
            "model_id": [None, t.model_id],
            "consent_id": [None, str(consent.id)],
            "source": [None, source],
        },
    )
    return FaceTemplateOut.model_validate(t)


@router.post(
    "/employees/{employee_id}/face-templates",
    response_model=FaceTemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {}, 409: {}, 413: {}, 422: {}, 503: {}},
    summary="Enrol a face from an uploaded image (image is not stored)",
)
async def enroll_upload(
    employee_id: uuid.UUID,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
    rec: RecognitionServiceDep,
    file: Annotated[UploadFile, File(description="JPEG or PNG with exactly one face, max 8 MB")],
) -> FaceTemplateOut:
    data = await file.read(MAX_IMAGE_BYTES + 1)
    return await _enroll(db, ctx, request, employee_id, data, rec, "upload")


@router.post(
    "/employees/{employee_id}/face-templates/from-camera",
    response_model=FaceTemplateOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {}, 409: {}, 422: {}, 502: {}, 503: {}},
    summary="Enrol a face from a camera's current frame (frame is not stored)",
)
async def enroll_from_camera(
    employee_id: uuid.UUID,
    body: FromCamera,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
    rec: RecognitionServiceDep,
    cams: CameraServiceDep,
) -> FaceTemplateOut:
    await _employee(db, employee_id)
    cam = (
        await db.execute(
            select(Camera).where(Camera.id == body.camera_id, Camera.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cam is None:
        raise not_found("Camera")
    frame = await cams.snapshot(source_spec(cam))
    return await _enroll(db, ctx, request, employee_id, frame, rec, f"camera:{cam.id}")


@router.delete(
    "/employees/{employee_id}/face-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}},
    summary="Delete one face template (hard delete)",
)
async def delete_template(
    employee_id: uuid.UUID,
    template_id: uuid.UUID,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
) -> None:
    t = (
        await db.execute(
            select(FaceEmbedding).where(
                FaceEmbedding.id == template_id, FaceEmbedding.employee_id == employee_id
            )
        )
    ).scalar_one_or_none()
    if t is None:
        raise not_found("Face template")
    await db.delete(t)
    await audit.record(
        db,
        action="BIOMETRIC_TEMPLATE_DELETED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=employee_id,
        ip=client_ip(request),
        changes={"template_id": [str(template_id), None]},
    )


# --- live recognition view (observation only, no attendance) --------------------------------


@router.get(
    "/recognition/live",
    responses={503: {}},
    summary="Recent recognition observations from cameras (in memory, not stored)",
)
async def live(
    _: BioReader,
    db: DbSession,
    cams: CameraServiceDep,
    since: Annotated[int, Query(ge=0)] = 0,
    camera_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    data = await cams.observations(since, str(camera_id) if camera_id else None)
    obs: list[dict[str, Any]] = list(data.get("observations") or [])
    ids = {
        uuid.UUID(str(o["employee_id"]))
        for o in obs
        if o.get("decision") == "MATCHED" and o.get("employee_id")
    }
    people: dict[str, dict[str, str]] = {}
    if ids:
        for e in (await db.execute(select(Employee).where(Employee.id.in_(ids)))).scalars():
            people[str(e.id)] = {
                "id": str(e.id),
                "employee_code": e.employee_code,
                "full_name": f"{e.last_name} {e.first_name}",
            }
    for o in obs:
        # Identity only for confident matches. Uncertain observations stay anonymous.
        emp_id = o.get("employee_id") if o.get("decision") == "MATCHED" else None
        o["employee"] = people.get(str(emp_id)) if emp_id else None
        o.pop("employee_id", None)
    names = {
        str(c.id): c.name
        for c in (await db.execute(select(Camera).where(Camera.deleted_at.is_(None)))).scalars()
    }
    cameras = [
        {**c, "name": names.get(str(c.get("camera_id")), "?")} for c in data.get("cameras") or []
    ]
    return {"observations": obs, "next": data.get("next", since), "cameras": cameras}
