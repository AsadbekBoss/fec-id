"""Employee queries shared by the API: biometric status, department trees, output mapping."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, case, false, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import BiometricConsent, Department, Employee, FaceEmbedding, SystemSetting
from app.models.enums import AttendanceMethod, EmployeeStatus
from app.schemas.employees import (
    BiometricStatus,
    BiometricSummary,
    DepartmentRef,
    EmployeeOut,
)

DEFAULT_CONSENT_VERSION = "2026-11-v1"


def latest_consent_lateral() -> Any:
    """LATERAL subquery: the most recent consent of the outer employee row."""
    c = aliased(BiometricConsent)
    return (
        select(
            c.id.label("consent_id"),
            c.granted_at.label("granted_at"),
            c.revoked_at.label("revoked_at"),
            (c.signed_document_key.is_not(None)).label("documented"),
        )
        .where(c.employee_id == Employee.id)
        .order_by(c.created_at.desc(), c.id.desc())
        .limit(1)
        .lateral("lc")
    )


def template_count_subquery() -> Any:
    return (
        select(func.count())
        .select_from(FaceEmbedding)
        .where(FaceEmbedding.employee_id == Employee.id, FaceEmbedding.is_active.is_(True))
        .correlate(Employee)
        .scalar_subquery()
    )


def biometric_status_expr(lc: Any, templates: Any) -> Any:
    return case(
        (
            Employee.attendance_method == AttendanceMethod.MANUAL,
            BiometricStatus.NOT_APPLICABLE.value,
        ),
        (lc.c.consent_id.is_(None), BiometricStatus.NO_CONSENT.value),
        (lc.c.revoked_at.is_not(None), BiometricStatus.CONSENT_REVOKED.value),
        (lc.c.documented.is_not(True), BiometricStatus.CONSENT_PENDING_DOCUMENT.value),
        (templates > 0, BiometricStatus.ENROLLED.value),
        else_=BiometricStatus.READY_FOR_ENROLLMENT.value,
    )


def employee_select() -> tuple[Select[Any], Any]:
    """SELECT employee + department name + biometric columns. Returns (stmt, status_expr)."""
    lc = latest_consent_lateral()
    templates = template_count_subquery()
    status = biometric_status_expr(lc, templates)
    stmt = (
        select(
            Employee,
            Department.name.label("department_name"),
            lc.c.consent_id,
            lc.c.granted_at,
            lc.c.revoked_at,
            func.coalesce(lc.c.documented, false()).label("documented"),
            templates.label("templates"),
            status.label("biometric_status"),
        )
        .join(Department, Department.id == Employee.department_id)
        .outerjoin(lc, true())
        .where(Employee.deleted_at.is_(None))
    )
    return stmt, status


def to_out(row: Any) -> EmployeeOut:
    e: Employee = row.Employee
    status = BiometricStatus(row.biometric_status)
    active_consent = row.consent_id is not None and row.revoked_at is None
    summary = BiometricSummary(
        status=status,
        consent_id=row.consent_id if active_consent else None,
        consent_granted_at=row.granted_at if active_consent else None,
        consent_documented=bool(active_consent and row.documented),
        face_templates=int(row.templates or 0),
        can_enroll=(
            status in (BiometricStatus.READY_FOR_ENROLLMENT, BiometricStatus.ENROLLED)
            and e.status != EmployeeStatus.TERMINATED
        ),
    )
    parts = [e.last_name, e.first_name] + ([e.middle_name] if e.middle_name else [])
    return EmployeeOut(
        id=e.id,
        employee_code=e.employee_code,
        last_name=e.last_name,
        first_name=e.first_name,
        middle_name=e.middle_name,
        full_name=" ".join(parts),
        branch_id=e.branch_id,
        department=DepartmentRef(id=e.department_id, name=row.department_name),
        work_schedule_id=e.work_schedule_id,
        position_title=e.position_title,
        phone=e.phone,
        email=e.email,
        hire_date=e.hire_date,
        termination_date=e.termination_date,
        status=e.status,
        is_active=e.status == EmployeeStatus.ACTIVE,
        attendance_method=e.attendance_method,
        has_photo=e.photo_key is not None,
        photo_updated_at=e.photo_updated_at,
        biometric=summary,
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


async def load_employee_out(db: AsyncSession, employee_id: uuid.UUID) -> EmployeeOut | None:
    stmt, _ = employee_select()
    row = (await db.execute(stmt.where(Employee.id == employee_id))).one_or_none()
    return to_out(row) if row else None


async def department_subtree_ids(db: AsyncSession, root_id: uuid.UUID) -> list[uuid.UUID]:
    """root + all descendants (non-deleted), via recursive CTE."""
    tree = (
        select(Department.id)
        .where(Department.id == root_id, Department.deleted_at.is_(None))
        .cte("tree", recursive=True)
    )
    child = aliased(Department)
    tree = tree.union_all(
        select(child.id).where(child.parent_id == tree.c.id, child.deleted_at.is_(None))
    )
    return list((await db.execute(select(tree.c.id))).scalars())


async def is_ancestor_or_self(
    db: AsyncSession, candidate_parent: uuid.UUID, department_id: uuid.UUID
) -> bool:
    """True if `candidate_parent` is `department_id` or one of its descendants (=> cycle)."""
    return candidate_parent in await department_subtree_ids(db, department_id)


async def current_consent_version(db: AsyncSession) -> str:
    value = (
        await db.execute(
            select(SystemSetting.value).where(SystemSetting.key == "biometric.consent_text_version")
        )
    ).scalar_one_or_none()
    return str(value) if value else DEFAULT_CONSENT_VERSION


async def active_consent(db: AsyncSession, employee_id: uuid.UUID) -> BiometricConsent | None:
    return (
        await db.execute(
            select(BiometricConsent)
            .where(
                BiometricConsent.employee_id == employee_id,
                BiometricConsent.revoked_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
