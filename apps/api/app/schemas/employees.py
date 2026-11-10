"""Request/response models for departments, employees and biometric consent."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.text import is_valid_name, normalize_name, normalize_phone
from app.models.enums import AttendanceMethod, EmployeeStatus
from app.schemas.common import StrictModel

EMPLOYEE_CODE_RE = re.compile(r"^[A-Z0-9_.-]{1,50}$")
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
DEPT_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,50}$")

Name100 = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


def _name(v: str | None, field: str) -> str | None:
    if v is None:
        return None
    v = normalize_name(v)
    if not v or not is_valid_name(v):
        raise ValueError(f"{field} may contain only letters, spaces, hyphens, dots and ʻ")
    return v


def _code(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().upper()
    if not EMPLOYEE_CODE_RE.fullmatch(v):
        raise ValueError("employee_code: 1-50 chars, letters, digits, _ . -")
    return v


def _phone(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    p = normalize_phone(v)
    if not PHONE_RE.fullmatch(p):
        raise ValueError("phone must have 7-15 digits, optionally starting with +")
    return p


def _email(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    e = v.strip().lower()
    if len(e) > 320 or not EMAIL_RE.fullmatch(e):
        raise ValueError("invalid email address")
    return e


def _hire_date(v: date | None) -> date | None:
    if v is None:
        return None
    if v < date(1950, 1, 1) or v > (datetime.now(UTC) + timedelta(days=366)).date():
        raise ValueError("hire_date must be between 1950-01-01 and one year from today")
    return v


# --- departments ------------------------------------------------------------------------------


class DepartmentCreate(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    code: Annotated[str, StringConstraints(pattern=DEPT_CODE_RE.pattern)] | None = None
    parent_id: uuid.UUID | None = None


class DepartmentUpdate(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    code: Annotated[str, StringConstraints(pattern=DEPT_CODE_RE.pattern)] | None = None
    parent_id: uuid.UUID | None = None
    head_employee_id: uuid.UUID | None = None
    work_schedule_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str | None
    parent_id: uuid.UUID | None
    head_employee_id: uuid.UUID | None
    work_schedule_id: uuid.UUID | None
    employee_count: int = 0
    created_at: datetime
    updated_at: datetime


class DepartmentRef(BaseModel):
    id: uuid.UUID
    name: str


# --- employees --------------------------------------------------------------------------------


class _EmployeeValidators(StrictModel):
    """Shared normalisation/validation for create and update."""

    @field_validator("employee_code", check_fields=False)
    @classmethod
    def _v_code(cls, v: str | None) -> str | None:
        return _code(v)

    @field_validator("last_name", "first_name", "middle_name", check_fields=False)
    @classmethod
    def _v_names(cls, v: str | None, info: ValidationInfo) -> str | None:
        return _name(v, info.field_name or "name")

    @field_validator("phone", check_fields=False)
    @classmethod
    def _v_phone(cls, v: str | None) -> str | None:
        return _phone(v)

    @field_validator("email", check_fields=False)
    @classmethod
    def _v_email(cls, v: str | None) -> str | None:
        return _email(v)

    @field_validator("hire_date", check_fields=False)
    @classmethod
    def _v_hire(cls, v: date | None) -> date | None:
        return _hire_date(v)


class EmployeeCreate(_EmployeeValidators):
    employee_code: str
    last_name: Name100
    first_name: Name100
    middle_name: Name100 | None = None
    department_id: uuid.UUID
    work_schedule_id: uuid.UUID | None = None
    position_title: Annotated[str, StringConstraints(max_length=150)] | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: date
    attendance_method: AttendanceMethod = AttendanceMethod.FACE


class EmployeeUpdate(_EmployeeValidators):
    """Partial update. Status changes use /activate and /deactivate. Explicit null clears an
    optional field."""

    employee_code: str | None = None
    last_name: Name100 | None = None
    first_name: Name100 | None = None
    middle_name: Name100 | None = None
    department_id: uuid.UUID | None = None
    work_schedule_id: uuid.UUID | None = None
    position_title: Annotated[str, StringConstraints(max_length=150)] | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: date | None = None
    attendance_method: AttendanceMethod | None = None

    @model_validator(mode="after")
    def _required_not_null(self) -> EmployeeUpdate:
        for f in (
            "employee_code",
            "last_name",
            "first_name",
            "department_id",
            "hire_date",
            "attendance_method",
        ):
            if f in self.model_fields_set and getattr(self, f) is None:
                raise ValueError(f"{f} cannot be null")
        if not self.model_fields_set:
            raise ValueError("no fields to update")
        return self


class DeactivationKind(StrEnum):
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class EmployeeDeactivate(StrictModel):
    status: DeactivationKind
    termination_date: date | None = None  # TERMINATED only; defaults to today
    reason: Reason

    @model_validator(mode="after")
    def _check(self) -> EmployeeDeactivate:
        if self.status is DeactivationKind.SUSPENDED and self.termination_date is not None:
            raise ValueError("termination_date is only allowed with status TERMINATED")
        return self


class EmployeeActivate(StrictModel):
    reason: Reason | None = None


class BiometricStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"  # attendance_method = MANUAL
    NO_CONSENT = "NO_CONSENT"
    CONSENT_PENDING_DOCUMENT = "CONSENT_PENDING_DOCUMENT"  # consent recorded, form not uploaded
    READY_FOR_ENROLLMENT = "READY_FOR_ENROLLMENT"  # documented consent, no face templates yet
    ENROLLED = "ENROLLED"
    CONSENT_REVOKED = "CONSENT_REVOKED"


class BiometricSummary(BaseModel):
    status: BiometricStatus
    consent_id: uuid.UUID | None = None
    consent_granted_at: datetime | None = None
    consent_documented: bool = False
    face_templates: int = 0
    can_enroll: bool = False


class EmployeeOut(BaseModel):
    id: uuid.UUID
    employee_code: str
    last_name: str
    first_name: str
    middle_name: str | None
    full_name: str
    department: DepartmentRef
    work_schedule_id: uuid.UUID | None
    position_title: str | None
    phone: str | None
    email: str | None
    hire_date: date
    termination_date: date | None
    status: EmployeeStatus
    is_active: bool
    attendance_method: AttendanceMethod
    has_photo: bool
    photo_updated_at: datetime | None
    biometric: BiometricSummary
    created_at: datetime
    updated_at: datetime


# --- consent ----------------------------------------------------------------------------------


class ConsentCreate(StrictModel):
    consent_text_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    # Explicit acknowledgement by the operator that the employee signed the form in person.
    employee_signed: Literal[True] = Field(
        description="Must be true: confirms the employee personally signed the consent form."
    )


class ConsentRevoke(StrictModel):
    reason: Reason


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    consent_text_version: str
    granted_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None
    recorded_by_user_id: uuid.UUID
    has_document: bool = False
    document_content_type: str | None
    document_uploaded_at: datetime | None
    is_active: bool
    created_at: datetime
