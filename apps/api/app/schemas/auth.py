from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.auth.passwords import MAX_LENGTH
from app.models.enums import UserRole
from app.schemas.common import StrictModel

USERNAME_PATTERN = r"^[A-Za-z0-9_.@-]{3,64}$"
EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s]{1,255}$"

Username = Annotated[str, StringConstraints(strip_whitespace=True, pattern=USERNAME_PATTERN)]
# Passwords are never stripped: whitespace is significant.
Password = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_LENGTH, strip_whitespace=False)
]
Email = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=320, pattern=EMAIL_PATTERN)
]
Locale = Literal["uz", "ru", "en"]


class LoginRequest(StrictModel):
    # Any 1..64 chars: a malformed username is simply "invalid credentials" (no format oracle).
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    password: Password


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str | None
    role: UserRole
    is_active: bool
    locale: str
    last_login_at: datetime | None
    created_at: datetime


class UserAdminOut(UserOut):
    """Extra fields visible to user administrators only."""

    failed_login_count: int
    locked_until: datetime | None
    password_changed_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: UserOut


class SessionInfo(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    permissions: list[str]
    session: SessionInfo


class ChangePasswordRequest(StrictModel):
    current_password: Password
    new_password: Password


class UserCreate(StrictModel):
    username: Username
    password: Password
    role: UserRole
    email: Email | None = None
    locale: Locale = "uz"


class UserUpdate(StrictModel):
    role: UserRole | None = None
    is_active: bool | None = None
    email: Email | None = None
    locale: Locale | None = None


class PasswordReset(StrictModel):
    new_password: Password
