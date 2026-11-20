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
import io
import struct
import time
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from PIL import Image
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
    # Head pose of the enrolled face (returned only, not stored); guided enrolment uses its sign.
    yaw_deg: float | None = None


# Guided enrolment ("look straight", "turn a little left / right"). Yaw comes from a landmark
# heuristic in the recognition service; the quality gate there rejects |yaw| > 30.
FRONTAL_MAX_YAW = 10.0
TURNED_MIN_YAW = 12.0


class FromCamera(StrictModel):
    camera_id: uuid.UUID
    # Optional pose the face must have, checked on the frame that is actually enrolled.
    pose: Literal["frontal", "turned"] | None = None
    # For "turned": the required side (sign of yaw). Omitted = either side.
    yaw_sign: Literal[-1, 1] | None = None


def pose_problem(yaw: float | None, pose: str | None, sign: int | None) -> str | None:
    """None when the yaw fits the requested pose, otherwise a short reason code."""
    if pose is None:
        return None
    if yaw is None:
        return "pose_unknown"
    if pose == "frontal":
        return None if abs(yaw) <= FRONTAL_MAX_YAW else "not_frontal"
    if abs(yaw) < TURNED_MIN_YAW:
        return "not_turned"
    if sign is not None and (yaw > 0) != (sign > 0):
        return "wrong_side"
    return None


async def _employee(db: DbSession, ctx: AuthContext, employee_id: uuid.UUID) -> Employee:
    e = (
        await db.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.deleted_at.is_(None),
                ctx.scope(Employee.branch_id),
            )
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
    employee_id: uuid.UUID, ctx: BioReader, db: DbSession
) -> list[FaceTemplateOut]:
    await _employee(db, ctx, employee_id)
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
    pose: str | None = None,
    yaw_sign: int | None = None,
) -> FaceTemplateOut:
    emp = await _employee(db, ctx, employee_id)
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
    yaw = result.get("yaw_deg")
    yaw = None if yaw is None else float(yaw)
    problem = pose_problem(yaw, pose, yaw_sign)
    if problem:
        raise AppError(
            422,
            "POSE_MISMATCH",
            "Head pose does not match",
            "The head pose does not match the requested step.",
            errors=[{"field": "pose", "code": problem, "message": f"yaw={yaw}"}],
        )
    nearest = result.get("nearest") or None
    if nearest and nearest.get("same_person_likely") and nearest.get("employee_id") != str(emp.id):
        other = await db.get(Employee, uuid.UUID(str(nearest["employee_id"])))
        # Never name an employee of a branch the caller cannot see.
        who = "another"
        if other is not None:
            who = (
                f"{other.employee_code} {other.last_name} {other.first_name}"
                if ctx.sees(other.branch_id)
                else "an employee of another branch"
            )
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
    out = FaceTemplateOut.model_validate(t)
    out.yaw_deg = yaw
    return out


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
    await _employee(db, ctx, employee_id)
    cam = (
        await db.execute(
            select(Camera).where(
                Camera.id == body.camera_id,
                Camera.deleted_at.is_(None),
                ctx.scope(Camera.branch_id),
            )
        )
    ).scalar_one_or_none()
    if cam is None:
        raise not_found("Camera")
    frame = await cams.snapshot(source_spec(cam))
    return await _enroll(
        db, ctx, request, employee_id, frame, rec, f"camera:{cam.id}", body.pose, body.yaw_sign
    )


# --- guided enrolment preview ---------------------------------------------------------------

PREVIEW_MAX_SIDE = 480
PREVIEW_AUDIT_SECONDS = 300
_preview_audited: dict[tuple[uuid.UUID, uuid.UUID], float] = {}


STREAM_FPS = 15
STREAM_MAX_SIDE = 640
STREAM_SECONDS = 120  # the page reconnects; bounds an abandoned connection


@router.get(
    "/employees/{employee_id}/face-templates/scan-stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"multipart/x-mixed-replace": {}}},
        404: {},
        409: {},
        429: {},
        503: {},
    },
    summary="Guided enrolment: smooth live preview (MJPEG, ~15 fps, nothing stored)",
)
async def scan_stream(
    employee_id: uuid.UUID,
    camera_id: uuid.UUID,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
    cams: CameraServiceDep,
) -> StreamingResponse:
    """Relays the camera service's in-memory preview of a MONITORED camera. No identification,
    no embeddings, no storage. Audited like scan-frame (once per user and camera every 5 min).
    The DB session closes before streaming starts (DbSession has function scope)."""
    await _employee(db, ctx, employee_id)
    cam = (
        await db.execute(
            select(Camera).where(
                Camera.id == camera_id, Camera.deleted_at.is_(None), ctx.scope(Camera.branch_id)
            )
        )
    ).scalar_one_or_none()
    if cam is None:
        raise not_found("Camera")
    content_type, chunks = await cams.stream(
        source_spec(cam), STREAM_FPS, STREAM_MAX_SIDE, STREAM_SECONDS
    )
    await _audit_preview(db, ctx, request, employee_id, cam.id)
    return StreamingResponse(chunks, media_type=content_type, headers={"Cache-Control": "no-store"})


async def _audit_preview(
    db: DbSession, ctx: AuthContext, request: Request, employee_id: uuid.UUID, camera_id: uuid.UUID
) -> None:
    key, now = (ctx.user_id, camera_id), time.monotonic()
    if now - _preview_audited.get(key, -1e9) < PREVIEW_AUDIT_SECONDS:
        return
    _preview_audited[key] = now
    await audit.record(
        db,
        action="ENROLMENT_PREVIEW_VIEWED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=employee_id,
        ip=client_ip(request),
        changes={"camera_id": [None, str(camera_id)]},
    )


class ScanCamera(StrictModel):
    camera_id: uuid.UUID
    # False when the page shows the MJPEG stream and only needs the face box and pose.
    preview: bool = True


class ScanFace(BaseModel):
    box: list[float]  # [x, y, w, h] as fractions of the frame (0..1)
    yaw_deg: float | None
    roll_deg: float | None
    passed: bool
    reasons: list[str]
    face_width_px: float


class ScanFrameOut(BaseModel):
    preview: str | None  # data:image/jpeg;base64,... small preview, not stored anywhere
    faces: int
    face: ScanFace | None  # the largest face


def _preview(jpeg: bytes) -> str:
    with Image.open(io.BytesIO(jpeg)) as im:
        img = im.convert("RGB")
    img.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)  # re-encoded: no metadata
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@router.post(
    "/employees/{employee_id}/face-templates/scan-frame",
    response_model=ScanFrameOut,
    responses={404: {}, 502: {}, 503: {}},
    summary="Guided enrolment: live preview frame with face box and head pose (nothing stored)",
)
async def scan_frame(
    employee_id: uuid.UUID,
    body: ScanCamera,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
    rec: RecognitionServiceDep,
    cams: CameraServiceDep,
) -> ScanFrameOut:
    """The frame is processed in memory only. No identification, no embeddings. Viewing is
    audited once per user and camera every 5 minutes (the page polls several times a second)."""
    await _employee(db, ctx, employee_id)
    cam = (
        await db.execute(
            select(Camera).where(
                Camera.id == body.camera_id,
                Camera.deleted_at.is_(None),
                ctx.scope(Camera.branch_id),
            )
        )
    ).scalar_one_or_none()
    if cam is None:
        raise not_found("Camera")
    frame = await cams.snapshot(source_spec(cam))
    q = await rec.quality(frame)
    preview = None
    if body.preview:
        try:
            preview = await anyio.to_thread.run_sync(_preview, frame)
        except (OSError, ValueError):
            raise AppError(
                502, "BAD_FRAME", "Bad gateway", "The camera returned an unreadable frame."
            ) from None
    del frame

    faces = [f for f in q.get("faces") or [] if isinstance(f, dict)]
    size = q.get("image_size") or [0, 0]
    w, h = float(size[0] or 0), float(size[1] or 0)
    best = max(faces, key=lambda f: float(f["box"][2]) * float(f["box"][3]), default=None)
    face = None
    if best is not None and w > 0 and h > 0:
        x, y, bw, bh = (float(v) for v in best["box"])
        qual = best.get("quality") or {}
        face = ScanFace(
            box=[round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)],
            yaw_deg=qual.get("yaw_deg"),
            roll_deg=qual.get("roll_deg"),
            passed=bool(qual.get("passed")),
            reasons=[str(r) for r in qual.get("reasons") or []],
            face_width_px=float(qual.get("face_width_px") or 0),
        )

    await _audit_preview(db, ctx, request, employee_id, cam.id)
    return ScanFrameOut(preview=preview, faces=len(faces), face=face)


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
    await _employee(db, ctx, employee_id)
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
    ctx: BioReader,
    db: DbSession,
    cams: CameraServiceDep,
    since: Annotated[int, Query(ge=0)] = 0,
    camera_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    data = await cams.observations(since, str(camera_id) if camera_id else None)
    names = {
        str(c.id): c.name
        for c in (
            await db.execute(
                select(Camera).where(Camera.deleted_at.is_(None), ctx.scope(Camera.branch_id))
            )
        ).scalars()
    }
    # Only cameras of the caller's branch(es).
    obs: list[dict[str, Any]] = [
        o for o in data.get("observations") or [] if str(o.get("camera_id")) in names
    ]
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
    cameras = [
        {**c, "name": names[str(c.get("camera_id"))]}
        for c in data.get("cameras") or []
        if str(c.get("camera_id")) in names
    ]
    return {"observations": obs, "next": data.get("next", since), "cameras": cameras}
