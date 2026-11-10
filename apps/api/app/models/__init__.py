"""Import every model so Base.metadata is complete (Alembic autogenerate, tests)."""

from app.models.attendance import AttendanceDaily, AttendanceEvent
from app.models.auth import AuthSession, RefreshToken
from app.models.biometrics import BiometricConsent, FaceEmbedding
from app.models.cameras import Camera
from app.models.organization import Department, Employee, User, WorkSchedule
from app.models.system import AuditLog, SystemSetting

__all__ = [
    "AttendanceDaily",
    "AttendanceEvent",
    "AuditLog",
    "AuthSession",
    "BiometricConsent",
    "Camera",
    "Department",
    "Employee",
    "FaceEmbedding",
    "RefreshToken",
    "SystemSetting",
    "User",
    "WorkSchedule",
]
