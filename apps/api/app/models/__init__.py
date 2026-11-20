"""Import every model so Base.metadata is complete (Alembic autogenerate, tests)."""

from app.models.attendance import AttendanceDaily, AttendanceEvent
from app.models.auth import AuthSession, RefreshToken
from app.models.biometrics import BiometricConsent, FaceEmbedding
from app.models.cameras import Camera
from app.models.notifications import NotificationLog, TelegramRecipient
from app.models.organization import Branch, Department, Employee, User, WorkSchedule
from app.models.system import AuditLog, SystemSetting

__all__ = [
    "AttendanceDaily",
    "AttendanceEvent",
    "AuditLog",
    "AuthSession",
    "BiometricConsent",
    "Branch",
    "Camera",
    "Department",
    "Employee",
    "FaceEmbedding",
    "NotificationLog",
    "RefreshToken",
    "SystemSetting",
    "TelegramRecipient",
    "User",
    "WorkSchedule",
]
