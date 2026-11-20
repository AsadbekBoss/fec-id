"""Role-based access control: roles map to permissions; endpoints declare permissions.

Endpoints must never check role names directly. They depend on `require(Permission.X)`, so
changing what a role may do is a one-line change here (and covered by tests).
"""

from enum import StrEnum

from app.models.enums import UserRole


class Permission(StrEnum):
    USERS_READ = "users:read"
    USERS_WRITE = "users:write"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"
    AUDIT_READ = "audit:read"
    SYSTEM_READ = "system:read"
    EMPLOYEES_READ = "employees:read"
    EMPLOYEES_WRITE = "employees:write"
    BIOMETRICS_READ = "biometrics:read"
    BIOMETRICS_WRITE = "biometrics:write"
    CAMERAS_READ = "cameras:read"
    CAMERAS_WRITE = "cameras:write"
    ATTENDANCE_READ = "attendance:read"
    ATTENDANCE_WRITE = "attendance:write"
    REPORTS_READ = "reports:read"
    # Telegram recipients of the user's branch (the bot itself and global switches: SETTINGS_*).
    NOTIFICATIONS_READ = "notifications:read"
    NOTIFICATIONS_WRITE = "notifications:write"
    # Every branch at once: create branches, system status, cross-branch data (SUPER_ADMIN only).
    BRANCHES_MANAGE = "branches:manage"


P = Permission

_VIEWER = frozenset(
    {P.SYSTEM_READ, P.EMPLOYEES_READ, P.CAMERAS_READ, P.ATTENDANCE_READ, P.REPORTS_READ}
)
_ADMIN = _VIEWER | frozenset(
    {
        P.USERS_READ,
        P.SETTINGS_READ,
        P.EMPLOYEES_WRITE,
        P.BIOMETRICS_READ,
        P.BIOMETRICS_WRITE,
        P.ATTENDANCE_WRITE,
        P.NOTIFICATIONS_READ,
        P.NOTIFICATIONS_WRITE,
    }
)
_SUPER_ADMIN = frozenset(Permission)

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.SUPER_ADMIN: _SUPER_ADMIN,
    UserRole.ADMIN: _ADMIN,
    UserRole.VIEWER: _VIEWER,
}


def permissions_for(role: UserRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())
