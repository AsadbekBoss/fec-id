"""Enumerations stored as native PostgreSQL ENUM types (DATABASE_SCHEMA §3)."""

from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum


class UserRole(StrEnum):
    """Owner-defined roles (Phase 4). Permissions per role: app/auth/permissions.py."""

    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    VIEWER = "VIEWER"


class EmployeeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ON_LEAVE = "ON_LEAVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class AttendanceMethod(StrEnum):
    FACE = "FACE"
    MANUAL = "MANUAL"  # for employees who decline biometric processing (ARCHITECTURE §9.1)


class CameraVendor(StrEnum):
    HIKVISION = "HIKVISION"
    DAHUA = "DAHUA"
    GENERIC_ONVIF = "GENERIC_ONVIF"
    GENERIC = "GENERIC"


class CameraDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"
    BOTH = "BOTH"
    NONE = "NONE"


class CameraProvider(StrEnum):
    """How frames are obtained. Providers are replaceable (apps/camera/camera/providers)."""

    RTSP = "RTSP"  # IP camera
    USB = "USB"  # local webcam on the camera-service host
    TEST = "TEST"  # synthetic / replayed frames for development without hardware


class CameraQualityStatus(StrEnum):
    """Result of the last stream quality test. A stream is never assumed suitable."""

    UNTESTED = "UNTESTED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"  # stream OK but no face could be assessed


class CameraStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"


class EventSource(StrEnum):
    CAMERA = "CAMERA"
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"


class RecognitionDecision(StrEnum):
    MATCHED = "MATCHED"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"
    SPOOF_SUSPECTED = "SPOOF_SUSPECTED"


class PunchDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    LATE = "LATE"
    INCOMPLETE = "INCOMPLETE"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"
    HOLIDAY = "HOLIDAY"
    WEEKEND = "WEEKEND"
    NOT_SCHEDULED = "NOT_SCHEDULED"


class ActorType(StrEnum):
    USER = "USER"
    SERVICE = "SERVICE"
    SYSTEM = "SYSTEM"


def pg_enum(enum_cls: type[StrEnum], name: str, **kw: Any) -> SAEnum:
    """Native PG enum storing the enum *values*."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
        **kw,
    )
