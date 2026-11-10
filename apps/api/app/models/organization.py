"""Departments, work schedules, employees, users."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey
from app.models.enums import AttendanceMethod, EmployeeStatus, UserRole, pg_enum

if TYPE_CHECKING:
    from app.models.attendance import AttendanceDaily, AttendanceEvent
    from app.models.biometrics import BiometricConsent, FaceEmbedding


class Department(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    __tablename__ = "departments"

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), unique=True)
    head_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # employees -> departments and departments -> employees form a cycle: add later.
        ForeignKey("employees.id", ondelete="SET NULL", use_alter=True),
    )
    # Default schedule for employees of this department (and its sub-departments) who have no
    # personal schedule. Resolution: employee -> department -> parent -> ... (revision 0004).
    work_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_schedules.id", ondelete="RESTRICT"), index=True
    )

    parent: Mapped[Department | None] = relationship(
        remote_side="Department.id", back_populates="children", lazy="raise"
    )
    children: Mapped[list[Department]] = relationship(back_populates="parent", lazy="raise")
    employees: Mapped[list[Employee]] = relationship(
        back_populates="department", foreign_keys="Employee.department_id", lazy="raise"
    )
    head: Mapped[Employee | None] = relationship(foreign_keys=[head_employee_id], lazy="raise")

    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        # Sibling names unique (case-insensitive) among non-deleted departments.
        Index(
            "uq_departments_parent_name_active",
            text("coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class WorkSchedule(UUIDPrimaryKey, Timestamps, Base):
    """Fixed weekly schedule (v1). Shift rotations / per-day rules come with the attendance
    engine phase (DATABASE_SCHEMA §8)."""

    __tablename__ = "work_schedules"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Asia/Tashkent"
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)  # <= start => crosses midnight
    working_days: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default="{1,2,3,4,5}"
    )  # ISO weekdays 1=Mon..7=Sun
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    grace_in_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    grace_out_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    early_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="180")
    late_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="360")
    overtime_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    employees: Mapped[list[Employee]] = relationship(back_populates="work_schedule", lazy="raise")

    __table_args__ = (
        CheckConstraint("start_time <> end_time", name="start_ne_end"),
        CheckConstraint(
            "cardinality(working_days) BETWEEN 1 AND 7 "
            "AND working_days <@ ARRAY[1,2,3,4,5,6,7]::smallint[]",
            name="working_days_valid",
        ),
        CheckConstraint("break_minutes BETWEEN 0 AND 720", name="break_range"),
        CheckConstraint("grace_in_minutes BETWEEN 0 AND 240", name="grace_in_range"),
        CheckConstraint("grace_out_minutes BETWEEN 0 AND 240", name="grace_out_range"),
        CheckConstraint("early_window_minutes BETWEEN 0 AND 720", name="early_window_range"),
        CheckConstraint("late_window_minutes BETWEEN 0 AND 720", name="late_window_range"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )


class Employee(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    """Employee master data.

    Data minimisation (ARCHITECTURE §9): no national ID, passport, birth date, home address,
    photo or gender is stored. Phone/email are optional.
    """

    __tablename__ = "employees"

    employee_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100))
    search_name: Mapped[str] = mapped_column(
        Text,
        Computed(
            "lower(last_name || ' ' || first_name || coalesce(' ' || middle_name, ''))",
            persisted=True,
        ),
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    work_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_schedules.id", ondelete="SET NULL"), index=True
    )
    position_title: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(CITEXT)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[EmployeeStatus] = mapped_column(
        pg_enum(EmployeeStatus, "employee_status"),
        nullable=False,
        server_default=EmployeeStatus.ACTIVE.value,
        index=True,
    )
    attendance_method: Mapped[AttendanceMethod] = mapped_column(
        pg_enum(AttendanceMethod, "attendance_method"),
        nullable=False,
        server_default=AttendanceMethod.FACE.value,
    )
    # Profile photo (identification in the UI only; NEVER used for face recognition).
    # Encrypted file in storage; see app/core/storage.py.
    photo_key: Mapped[str | None] = mapped_column(String(512))
    photo_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    department: Mapped[Department] = relationship(
        back_populates="employees", foreign_keys=[department_id], lazy="raise"
    )
    work_schedule: Mapped[WorkSchedule | None] = relationship(
        back_populates="employees", lazy="raise"
    )
    user: Mapped[User | None] = relationship(back_populates="employee", lazy="raise")
    consents: Mapped[list[BiometricConsent]] = relationship(back_populates="employee", lazy="raise")
    face_embeddings: Mapped[list[FaceEmbedding]] = relationship(
        back_populates="employee", passive_deletes=True, lazy="raise"
    )
    attendance_events: Mapped[list[AttendanceEvent]] = relationship(
        back_populates="employee", lazy="raise"
    )
    attendance_days: Mapped[list[AttendanceDaily]] = relationship(
        back_populates="employee", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("employee_code ~ '^[A-Za-z0-9_.-]{1,50}$'", name="code_format"),
        # Codes are stored upper-case so "e001" and "E001" cannot both exist.
        CheckConstraint("employee_code = upper(employee_code)", name="code_upper"),
        CheckConstraint(
            "(photo_key IS NULL) = (photo_updated_at IS NULL)", name="photo_fields_together"
        ),
        CheckConstraint(
            "length(btrim(last_name)) > 0 AND length(btrim(first_name)) > 0", name="names_present"
        ),
        CheckConstraint("phone IS NULL OR phone ~ '^\\+?[0-9]{7,15}$'", name="phone_format"),
        CheckConstraint("email IS NULL OR email ~ '^[^@\\s]+@[^@\\s]+$'", name="email_format"),
        CheckConstraint(
            "termination_date IS NULL OR termination_date >= hire_date",
            name="termination_after_hire",
        ),
        CheckConstraint(
            "(status = 'TERMINATED') = (termination_date IS NOT NULL)",
            name="terminated_has_date",
        ),
        Index(
            "ix_employees_search_name_trgm",
            "search_name",
            postgresql_using="gin",
            postgresql_ops={"search_name": "gin_trgm_ops"},
        ),
        # Duplicate prevention among non-deleted employees (email is citext: case-insensitive).
        Index(
            "uq_employees_email_active",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND email IS NOT NULL"),
        ),
        Index(
            "uq_employees_phone_active",
            "phone",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND phone IS NOT NULL"),
        ),
    )


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="RESTRICT"), unique=True
    )
    mfa_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    failed_login_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locale: Mapped[str] = mapped_column(String(5), nullable=False, server_default="uz")

    employee: Mapped[Employee | None] = relationship(back_populates="user", lazy="raise")

    __table_args__ = (
        CheckConstraint("username ~ '^[A-Za-z0-9_.@-]{3,64}$'", name="username_format"),
        # Only Argon2id hashes are accepted (ARCHITECTURE §7.1).
        CheckConstraint("password_hash LIKE '$argon2id$%'", name="password_argon2id"),
        CheckConstraint("NOT mfa_enabled OR mfa_secret_enc IS NOT NULL", name="mfa_has_secret"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_nonneg"),
        CheckConstraint("locale IN ('uz', 'ru', 'en')", name="locale_supported"),
    )
