"""/api/v1/employees: employee records, status, profile photo and biometric consent."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.api.errors import AppError, conflict, not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.core import crypto, storage, uploads
from app.core.config import get_settings
from app.core.text import escape_like, normalize_search
from app.db import audit
from app.models import BiometricConsent, Department, Employee, FaceEmbedding, WorkSchedule
from app.models.enums import ActorType, AttendanceMethod, EmployeeStatus
from app.schemas.common import Page
from app.schemas.employees import (
    BiometricStatus,
    ConsentCreate,
    ConsentOut,
    ConsentRevoke,
    DeactivationKind,
    EmployeeActivate,
    EmployeeCreate,
    EmployeeDeactivate,
    EmployeeOut,
    EmployeeUpdate,
)
from app.services import employees as svc

router = APIRouter(prefix="/employees", tags=["employees"])
biometrics_router = APIRouter(prefix="/biometrics", tags=["biometrics"])

Reader = Annotated[AuthContext, Depends(require(Permission.EMPLOYEES_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.EMPLOYEES_WRITE))]
BioReader = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_READ))]
BioWriter = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_WRITE))]

_UNIQUE_CONFLICTS = {
    "uq_employees_branch_id_employee_code": (
        "EMPLOYEE_CODE_EXISTS",
        "An employee with this code already exists in this branch.",
    ),
    "uq_employees_email_active": (
        "EMPLOYEE_EMAIL_EXISTS",
        "Another employee already uses this email.",
    ),
    "uq_employees_phone_active": (
        "EMPLOYEE_PHONE_EXISTS",
        "Another employee already uses this phone.",
    ),
}


# --- helpers ----------------------------------------------------------------------------------


async def _get_employee(
    db: DbSession, ctx: AuthContext, employee_id: uuid.UUID, lock: bool = False
) -> Employee:
    stmt = select(Employee).where(
        Employee.id == employee_id, Employee.deleted_at.is_(None), ctx.scope(Employee.branch_id)
    )
    if lock:
        stmt = stmt.with_for_update()
    emp = (await db.execute(stmt)).scalar_one_or_none()
    if emp is None:
        raise not_found("Employee")
    return emp


async def _department_branch(db: DbSession, ctx: AuthContext, department_id: Any) -> uuid.UUID:
    """The branch of a visible department (an employee belongs to its department's branch)."""
    d = await db.get(Department, department_id)
    if d is None or d.deleted_at is not None or not ctx.sees(d.branch_id):
        raise validation_error("department_id", "not_found", "Department not found.")
    return d.branch_id


async def _check_schedule(db: DbSession, branch_id: uuid.UUID, work_schedule_id: Any) -> None:
    if work_schedule_id is not None:
        ws = await db.get(WorkSchedule, work_schedule_id)
        if ws is None or ws.branch_id != branch_id:
            raise validation_error("work_schedule_id", "not_found", "Work schedule not found.")


async def _check_duplicates(
    db: DbSession,
    branch_id: uuid.UUID,
    code: str | None,
    email: str | None,
    phone: str | None,
    exclude: uuid.UUID | None = None,
) -> None:
    """Friendly 409s before insert/update. Unique indexes remain the real guarantee."""
    checks: list[tuple[Any, str]] = []
    if code is not None:  # unique per branch forever, also against deleted records
        checks.append(
            (
                (Employee.employee_code == code) & (Employee.branch_id == branch_id),
                "uq_employees_branch_id_employee_code",
            )
        )
    if email is not None:
        checks.append(
            ((Employee.email == email) & Employee.deleted_at.is_(None), "uq_employees_email_active")
        )
    if phone is not None:
        checks.append(
            ((Employee.phone == phone) & Employee.deleted_at.is_(None), "uq_employees_phone_active")
        )
    for cond, name in checks:
        stmt = select(Employee.id).where(cond)
        if exclude is not None:
            stmt = stmt.where(Employee.id != exclude)
        if (await db.execute(stmt.limit(1))).first():
            raise conflict(*_UNIQUE_CONFLICTS[name])


async def _flush(db: DbSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:
        msg = str(e.orig)
        for name, (code, detail) in _UNIQUE_CONFLICTS.items():
            if name in msg:
                raise conflict(code, detail) from None
        raise


async def _out(db: DbSession, employee_id: uuid.UUID) -> EmployeeOut:
    out = await svc.load_employee_out(db, employee_id)
    if out is None:
        raise not_found("Employee")
    return out


def _jsonable(v: Any) -> Any:
    if isinstance(v, (date, datetime, uuid.UUID)):
        return str(v)
    return getattr(v, "value", v)


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(
            413,
            "PAYLOAD_TOO_LARGE",
            "Payload too large",
            f"File exceeds {limit // (1024 * 1024)} MB.",
        )
    return data


def _upload_error(e: uploads.UploadRejected) -> AppError:
    if e.code == "too_large":
        return AppError(413, "PAYLOAD_TOO_LARGE", "Payload too large", e.message)
    return AppError(
        422,
        "INVALID_FILE",
        "Invalid file",
        e.message,
        errors=[{"field": "file", "code": e.code, "message": e.message}],
    )


def _consent_out(c: BiometricConsent) -> ConsentOut:
    out = ConsentOut.model_validate(c)
    out.has_document = c.signed_document_key is not None
    return out


# --- CRUD -------------------------------------------------------------------------------------


@router.get("", response_model=Page[EmployeeOut], summary="List / search / filter employees")
async def list_employees(
    ctx: Reader,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100, description="Name or code contains")] = None,
    department_id: uuid.UUID | None = None,
    include_subdepartments: bool = True,
    status_: Annotated[EmployeeStatus | None, Query(alias="status")] = None,
    attendance_method: AttendanceMethod | None = None,
    biometric_status: BiometricStatus | None = None,
    sort: Annotated[
        str, Query(pattern=r"^-?(last_name|employee_code|hire_date|created_at)$")
    ] = "last_name",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[EmployeeOut]:
    stmt, bio_status = svc.employee_select()
    conds: list[Any] = [ctx.scope(Employee.branch_id)]
    if q and q.strip():
        term = f"%{escape_like(normalize_search(q))}%"
        conds.append(
            or_(
                Employee.search_name.like(term, escape="\\"),
                Employee.employee_code.ilike(term, escape="\\"),
            )
        )
    if department_id is not None:
        ids = (
            await svc.department_subtree_ids(db, department_id)
            if include_subdepartments
            else [department_id]
        )
        conds.append(Employee.department_id.in_(ids or [department_id]))
    if status_ is not None:
        conds.append(Employee.status == status_)
    if attendance_method is not None:
        conds.append(Employee.attendance_method == attendance_method)
    if biometric_status is not None:
        conds.append(bio_status == biometric_status.value)
    stmt = stmt.where(*conds)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    field = getattr(Employee, sort.lstrip("-"))
    order = field.desc() if sort.startswith("-") else field.asc()
    rows = await db.execute(
        stmt.order_by(order, Employee.id).offset((page - 1) * page_size).limit(page_size)
    )
    return Page(items=[svc.to_out(r) for r in rows], page=page, page_size=page_size, total=total)


@router.post(
    "",
    response_model=EmployeeOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Duplicate code / email / phone"}, 422: {}},
    summary="Create employee",
)
async def create_employee(
    body: EmployeeCreate, ctx: Writer, request: Request, db: DbSession
) -> EmployeeOut:
    branch = await _department_branch(db, ctx, body.department_id)
    await _check_schedule(db, branch, body.work_schedule_id)
    await _check_duplicates(db, branch, body.employee_code, body.email, body.phone)
    emp = Employee(**body.model_dump(), branch_id=branch)
    db.add(emp)
    await _flush(db)
    await audit.record(
        db,
        action="EMPLOYEE_CREATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
        changes={k: [None, _jsonable(v)] for k, v in body.model_dump().items() if v is not None},
    )
    return await _out(db, emp.id)


@router.get(
    "/{employee_id}", response_model=EmployeeOut, responses={404: {}}, summary="Employee details"
)
async def get_employee(employee_id: uuid.UUID, ctx: Reader, db: DbSession) -> EmployeeOut:
    await _get_employee(db, ctx, employee_id)
    return await _out(db, employee_id)


@router.patch(
    "/{employee_id}",
    response_model=EmployeeOut,
    responses={404: {}, 409: {}, 422: {}},
    summary="Update employee (partial)",
)
async def update_employee(
    employee_id: uuid.UUID, body: EmployeeUpdate, ctx: Writer, request: Request, db: DbSession
) -> EmployeeOut:
    emp = await _get_employee(db, ctx, employee_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    branch = emp.branch_id
    if changes.get("department_id") is not None:
        # Moving to a department of another branch moves the employee to that branch (only a
        # user who sees both branches can do it). The schedule must belong to the new branch.
        branch = await _department_branch(db, ctx, changes["department_id"])
    await _check_schedule(db, branch, changes.get("work_schedule_id", emp.work_schedule_id))
    code = changes.get("employee_code") or (emp.employee_code if branch != emp.branch_id else None)
    await _check_duplicates(
        db, branch, code, changes.get("email"), changes.get("phone"), exclude=emp.id
    )
    if branch != emp.branch_id:
        changes["branch_id"] = branch
    new_hire = changes.get("hire_date", emp.hire_date)
    if emp.termination_date and new_hire > emp.termination_date:
        raise validation_error(
            "hire_date", "after_termination", "hire_date cannot be after termination_date."
        )
    if (
        changes.get("attendance_method") == AttendanceMethod.MANUAL
        and emp.attendance_method != AttendanceMethod.MANUAL
        and await svc.active_consent(db, emp.id) is not None
    ):
        raise conflict(
            "ACTIVE_CONSENT_EXISTS",
            "Revoke the biometric consent before switching to manual attendance.",
        )

    diff: dict[str, list[Any]] = {}
    for k, v in changes.items():
        old = getattr(emp, k)
        if old != v:
            diff[k] = [_jsonable(old), _jsonable(v)]
            setattr(emp, k, v)
    await _flush(db)
    if diff:
        await audit.record(
            db,
            action="EMPLOYEE_UPDATED",
            actor_type=ActorType.USER,
            actor_user_id=ctx.user_id,
            entity_type="employee",
            entity_id=emp.id,
            ip=client_ip(request),
            changes=diff,
        )
    await db.refresh(emp)
    return await _out(db, emp.id)


@router.post(
    "/{employee_id}/deactivate",
    response_model=EmployeeOut,
    responses={404: {}, 409: {"description": "Already in that status"}, 422: {}},
    summary="Suspend or terminate an employee",
)
async def deactivate_employee(
    employee_id: uuid.UUID,
    body: EmployeeDeactivate,
    ctx: Writer,
    request: Request,
    db: DbSession,
) -> EmployeeOut:
    emp = await _get_employee(db, ctx, employee_id, lock=True)
    target = EmployeeStatus(body.status.value)
    if emp.status == target:
        raise conflict("INVALID_STATUS_TRANSITION", f"Employee is already {target.value}.")
    old = emp.status
    if body.status is DeactivationKind.TERMINATED:
        term = body.termination_date or datetime.now(UTC).date()
        if term < emp.hire_date:
            raise validation_error(
                "termination_date", "before_hire", "termination_date cannot be before hire_date."
            )
        emp.termination_date = term
    else:
        emp.termination_date = None
    emp.status = target
    await _flush(db)
    await audit.record(
        db,
        action="EMPLOYEE_DEACTIVATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
        changes={
            "status": [old.value, target.value],
            "reason": [None, body.reason],
            "termination_date": [None, _jsonable(emp.termination_date)],
        },
    )
    await db.refresh(emp)
    return await _out(db, emp.id)


@router.post(
    "/{employee_id}/activate",
    response_model=EmployeeOut,
    responses={404: {}, 409: {"description": "Already active"}},
    summary="Re-activate employee",
)
async def activate_employee(
    employee_id: uuid.UUID,
    ctx: Writer,
    request: Request,
    db: DbSession,
    body: EmployeeActivate | None = None,
) -> EmployeeOut:
    emp = await _get_employee(db, ctx, employee_id, lock=True)
    if emp.status == EmployeeStatus.ACTIVE:
        raise conflict("INVALID_STATUS_TRANSITION", "Employee is already ACTIVE.")
    old = emp.status
    emp.status = EmployeeStatus.ACTIVE
    emp.termination_date = None
    await _flush(db)
    await audit.record(
        db,
        action="EMPLOYEE_ACTIVATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
        changes={"status": [old.value, "ACTIVE"], "reason": [None, body.reason if body else None]},
    )
    await db.refresh(emp)
    return await _out(db, emp.id)


# --- profile photo ----------------------------------------------------------------------------


@router.put(
    "/{employee_id}/photo",
    response_model=EmployeeOut,
    responses={404: {}, 413: {}, 422: {"description": "Not a valid JPEG/PNG"}},
    summary="Upload or replace the profile photo (JPEG/PNG, re-encoded, EXIF removed)",
)
async def put_photo(
    employee_id: uuid.UUID,
    ctx: Writer,
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File(description="JPEG or PNG, max 5 MB")],
) -> EmployeeOut:
    emp = await _get_employee(db, ctx, employee_id, lock=True)
    limit = get_settings().max_photo_bytes
    try:
        clean = await uploads.clean_photo(await _read_upload(file, limit), limit)
    except uploads.UploadRejected as e:
        raise _upload_error(e) from None
    key = storage.new_key("photos", emp.id)
    await storage.put(key, clean.data, crypto.Purpose.PROFILE_PHOTO)
    old_key = emp.photo_key
    emp.photo_key, emp.photo_updated_at = key, datetime.now(UTC)
    try:
        await _flush(db)
    except Exception:
        await storage.delete(key)
        raise
    await audit.record(
        db,
        action="EMPLOYEE_PHOTO_UPDATED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
    )
    # Delete the old file now (privacy: don't keep superseded photos). The row update was already
    # flushed successfully. In the rare case the final COMMIT fails, the row keeps the old key
    # and GET /photo returns 404 for the missing file. The admin simply uploads again.
    await storage.delete(old_key)
    await db.refresh(emp)
    return await _out(db, emp.id)


@router.get(
    "/{employee_id}/photo",
    responses={200: {"content": {"image/jpeg": {}}}, 404: {}},
    response_class=Response,
    summary="Download profile photo",
)
async def get_photo(employee_id: uuid.UUID, ctx: Reader, db: DbSession) -> Response:
    emp = await _get_employee(db, ctx, employee_id)
    if not emp.photo_key:
        raise not_found("Photo")
    try:
        data = await storage.get(emp.photo_key, crypto.Purpose.PROFILE_PHOTO)
    except storage.StorageError:
        raise not_found("Photo") from None
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )


@router.delete(
    "/{employee_id}/photo",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {}},
    summary="Remove profile photo",
)
async def delete_photo(
    employee_id: uuid.UUID, ctx: Writer, request: Request, db: DbSession
) -> None:
    emp = await _get_employee(db, ctx, employee_id, lock=True)
    if not emp.photo_key:
        raise not_found("Photo")
    old_key = emp.photo_key
    emp.photo_key = emp.photo_updated_at = None
    await _flush(db)
    await audit.record(
        db,
        action="EMPLOYEE_PHOTO_DELETED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
    )
    await storage.delete(old_key)


# --- biometric consent ------------------------------------------------------------------------


@biometrics_router.get("/consent-version", summary="Consent wording version that must be signed")
async def consent_version(_: BioReader, db: DbSession) -> dict[str, str]:
    return {"consent_text_version": await svc.current_consent_version(db)}


@router.get(
    "/{employee_id}/consents",
    response_model=list[ConsentOut],
    responses={404: {}},
    summary="Consent history (newest first)",
)
async def list_consents(employee_id: uuid.UUID, ctx: BioReader, db: DbSession) -> list[ConsentOut]:
    await _get_employee(db, ctx, employee_id)
    rows = (
        await db.execute(
            select(BiometricConsent)
            .where(BiometricConsent.employee_id == employee_id)
            .order_by(BiometricConsent.created_at.desc(), BiometricConsent.id.desc())
        )
    ).scalars()
    return [_consent_out(c) for c in rows]


@router.post(
    "/{employee_id}/consents",
    response_model=ConsentOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {}, 409: {}, 422: {}},
    summary="Record that the employee signed the biometric consent form",
)
async def create_consent(
    employee_id: uuid.UUID, body: ConsentCreate, ctx: BioWriter, request: Request, db: DbSession
) -> ConsentOut:
    emp = await _get_employee(db, ctx, employee_id, lock=True)
    if emp.status == EmployeeStatus.TERMINATED:
        raise conflict("EMPLOYEE_TERMINATED", "Cannot record consent for a terminated employee.")
    if emp.attendance_method != AttendanceMethod.FACE:
        raise conflict(
            "BIOMETRICS_NOT_APPLICABLE",
            "Employee uses manual attendance. Set attendance_method to FACE first.",
        )
    expected = await svc.current_consent_version(db)
    if body.consent_text_version != expected:
        raise validation_error(
            "consent_text_version",
            "version_mismatch",
            f"The current consent form version is '{expected}'.",
        )
    if await svc.active_consent(db, emp.id) is not None:
        raise conflict("CONSENT_ALREADY_ACTIVE", "Employee already has an active consent.")
    c = BiometricConsent(
        employee_id=emp.id,
        consent_text_version=body.consent_text_version,
        recorded_by_user_id=ctx.user_id,
    )
    db.add(c)
    await _flush(db)
    await audit.record(
        db,
        action="BIOMETRIC_CONSENT_RECORDED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=emp.id,
        ip=client_ip(request),
        changes={"consent_id": [None, str(c.id)], "version": [None, c.consent_text_version]},
    )
    await db.refresh(c)
    return _consent_out(c)


async def _get_consent(
    db: DbSession, employee_id: uuid.UUID, consent_id: uuid.UUID
) -> BiometricConsent:
    c = (
        await db.execute(
            select(BiometricConsent)
            .where(BiometricConsent.id == consent_id, BiometricConsent.employee_id == employee_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if c is None:
        raise not_found("Consent")
    return c


@router.put(
    "/{employee_id}/consents/{consent_id}/document",
    response_model=ConsentOut,
    responses={
        404: {},
        409: {"description": "Revoked, or document already attached"},
        413: {},
        422: {},
    },
    summary="Attach the signed consent form (PDF/JPEG/PNG). Evidence is immutable once attached.",
)
async def put_consent_document(
    employee_id: uuid.UUID,
    consent_id: uuid.UUID,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File(description="Signed form: PDF, JPEG or PNG, max 10 MB")],
) -> ConsentOut:
    await _get_employee(db, ctx, employee_id)
    c = await _get_consent(db, employee_id, consent_id)
    if c.revoked_at is not None:
        raise conflict("CONSENT_REVOKED", "Consent has been revoked.")
    if c.signed_document_key is not None:
        raise conflict(
            "DOCUMENT_ALREADY_ATTACHED",
            "The signed form is evidence and cannot be replaced. "
            "Revoke and record a new consent instead.",
        )
    limit = get_settings().max_document_bytes
    try:
        clean = await uploads.clean_document(await _read_upload(file, limit), limit)
    except uploads.UploadRejected as e:
        raise _upload_error(e) from None
    key = storage.new_key("consent_documents", employee_id)
    await storage.put(key, clean.data, crypto.Purpose.CONSENT_DOCUMENT)
    c.signed_document_key = key
    c.document_content_type = clean.content_type
    c.document_uploaded_at = datetime.now(UTC)
    try:
        await _flush(db)
    except Exception:
        await storage.delete(key)
        raise
    await audit.record(
        db,
        action="BIOMETRIC_CONSENT_DOCUMENTED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=employee_id,
        ip=client_ip(request),
        changes={"consent_id": [None, str(c.id)], "content_type": [None, clean.content_type]},
    )
    await db.refresh(c)
    return _consent_out(c)


@router.get(
    "/{employee_id}/consents/{consent_id}/document",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/jpeg": {}}}, 404: {}},
    summary="Download the signed consent form (audited)",
)
async def get_consent_document(
    employee_id: uuid.UUID,
    consent_id: uuid.UUID,
    ctx: BioReader,
    request: Request,
    db: DbSession,
) -> Response:
    await _get_employee(db, ctx, employee_id)
    c = (
        await db.execute(
            select(BiometricConsent).where(
                BiometricConsent.id == consent_id, BiometricConsent.employee_id == employee_id
            )
        )
    ).scalar_one_or_none()
    if c is None or not c.signed_document_key or not c.document_content_type:
        raise not_found("Consent document")
    try:
        data = await storage.get(c.signed_document_key, crypto.Purpose.CONSENT_DOCUMENT)
    except storage.StorageError:
        raise not_found("Consent document") from None
    await audit.record(
        db,
        action="BIOMETRIC_CONSENT_DOCUMENT_VIEWED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=employee_id,
        ip=client_ip(request),
        changes={"consent_id": [None, str(c.id)]},
    )
    ext = "pdf" if c.document_content_type == "application/pdf" else "jpg"
    return Response(
        data,
        media_type=c.document_content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="consent-{c.id}.{ext}"',
        },
    )


@router.post(
    "/{employee_id}/consents/{consent_id}/revoke",
    response_model=ConsentOut,
    responses={404: {}, 409: {"description": "Already revoked"}, 422: {}},
    summary="Revoke consent. All face templates under it are deleted in the same transaction.",
)
async def revoke_consent(
    employee_id: uuid.UUID,
    consent_id: uuid.UUID,
    body: ConsentRevoke,
    ctx: BioWriter,
    request: Request,
    db: DbSession,
) -> ConsentOut:
    await _get_employee(db, ctx, employee_id)
    c = await _get_consent(db, employee_id, consent_id)
    if c.revoked_at is not None:
        raise conflict("CONSENT_REVOKED", "Consent is already revoked.")
    purged = (
        await db.execute(
            select(func.count()).select_from(FaceEmbedding).where(FaceEmbedding.consent_id == c.id)
        )
    ).scalar_one()
    c.revoked_at = datetime.now(UTC)
    c.revocation_reason = body.reason
    await _flush(db)  # DB trigger deletes the face templates here
    await audit.record(
        db,
        action="BIOMETRIC_CONSENT_REVOKED",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="employee",
        entity_id=employee_id,
        ip=client_ip(request),
        changes={
            "consent_id": [None, str(c.id)],
            "reason": [None, body.reason],
            "templates_deleted": [None, purged],
        },
    )
    await db.refresh(c)
    return _consent_out(c)
