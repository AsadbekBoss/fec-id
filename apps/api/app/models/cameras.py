"""Cameras (Phase 10: provider-based).

* `provider` decides which fields apply: RTSP (host, port, stream paths, optional credentials),
  USB (local `device` on the camera-service host) and TEST (synthetic frames, `settings.test`).
* Credentials are stored only encrypted (AES-256-GCM, AAD bound to the row and field). They are
  write-only through the API and never appear in a stream path.
* `quality_status` starts UNTESTED. A stream is never assumed suitable for face recognition
  until a quality test has passed.
* `runtime` holds the last status report of the camera service (fps, frames, reconnects).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import crypto
from app.db.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey
from app.models.enums import (
    CameraDirection,
    CameraProvider,
    CameraQualityStatus,
    CameraStatus,
    CameraVendor,
    pg_enum,
)
from app.models.organization import branch_fk

if TYPE_CHECKING:
    from app.models.attendance import AttendanceEvent

USB_DEVICE_PATTERN = r"^([0-9]{1,2}|/dev/video[0-9]{1,2})$"


class Camera(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    __tablename__ = "cameras"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Records attendance only for employees of this branch (others: OTHER_BRANCH).
    branch_id: Mapped[uuid.UUID] = branch_fk()
    # Departments this camera records attendance for (sub-departments included). Empty = all.
    # Faces are always matched against EVERY employee; others are logged as OTHER_DEPARTMENT.
    department_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    location: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[CameraProvider] = mapped_column(
        pg_enum(CameraProvider, "camera_provider"),
        nullable=False,
        server_default=CameraProvider.RTSP.value,
    )
    vendor: Mapped[CameraVendor] = mapped_column(
        pg_enum(CameraVendor, "camera_vendor"),
        nullable=False,
        server_default=CameraVendor.GENERIC.value,
    )
    model: Mapped[str | None] = mapped_column(String(100))
    host: Mapped[str | None] = mapped_column(String(255))
    rtsp_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="554")
    onvif_port: Mapped[int | None] = mapped_column(Integer)
    main_stream_path: Mapped[str | None] = mapped_column(String(512))
    sub_stream_path: Mapped[str | None] = mapped_column(String(512))
    device: Mapped[str | None] = mapped_column(String(32))
    username_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    direction: Mapped[CameraDirection] = mapped_column(
        pg_enum(CameraDirection, "camera_direction"),
        nullable=False,
        server_default=CameraDirection.NONE.value,
    )
    recognition_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[CameraStatus] = mapped_column(
        pg_enum(CameraStatus, "camera_status"),
        nullable=False,
        server_default=CameraStatus.DRAFT.value,
        index=True,
    )
    roi: Mapped[list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    runtime: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    runtime_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality_status: Mapped[CameraQualityStatus] = mapped_column(
        pg_enum(CameraQualityStatus, "camera_quality_status"),
        nullable=False,
        server_default=CameraQualityStatus.UNTESTED.value,
    )
    quality_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    attendance_events: Mapped[list[AttendanceEvent]] = relationship(
        back_populates="camera", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("rtsp_port BETWEEN 1 AND 65535", name="rtsp_port_range"),
        CheckConstraint(
            "onvif_port IS NULL OR onvif_port BETWEEN 1 AND 65535", name="onvif_port_range"
        ),
        CheckConstraint("host IS NULL OR host ~ '^[A-Za-z0-9.:-]{1,255}$'", name="host_format"),
        # Credentials must never be embedded in stream paths (ARCHITECTURE §5).
        CheckConstraint(
            "(main_stream_path IS NULL OR (position('@' in main_stream_path) = 0 "
            "AND position('://' in main_stream_path) = 0)) "
            "AND (sub_stream_path IS NULL OR (position('@' in sub_stream_path) = 0 "
            "AND position('://' in sub_stream_path) = 0))",
            name="stream_paths_no_credentials",
        ),
        CheckConstraint(
            "(username_enc IS NULL) = (password_enc IS NULL) AND (username_enc IS NULL OR "
            f"(octet_length(username_enc) >= {crypto.MIN_CIPHERTEXT_LEN} "
            f"AND octet_length(password_enc) >= {crypto.MIN_CIPHERTEXT_LEN}))",
            name="credentials_encrypted",
        ),
        CheckConstraint(
            "(provider <> 'RTSP' OR (host IS NOT NULL AND main_stream_path IS NOT NULL)) "
            "AND (provider <> 'USB' OR device IS NOT NULL) "
            "AND (provider = 'RTSP' OR username_enc IS NULL)",
            name="provider_fields",
        ),
        CheckConstraint(f"device IS NULL OR device ~ '{USB_DEVICE_PATTERN}'", name="device_format"),
        CheckConstraint("jsonb_typeof(settings) = 'object'", name="settings_is_object"),
        CheckConstraint("roi IS NULL OR jsonb_typeof(roi) = 'array'", name="roi_is_array"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        Index(
            "uq_cameras_name_active",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # --- credential helpers ----------------------------------------------------------------
    def _aad(self, field: str) -> str:
        return f"cameras:{self.id}:{field}"

    @property
    def has_credentials(self) -> bool:
        return self.username_enc is not None

    def set_credentials(
        self, username: str, password: str, keyring: crypto.Keyring | None = None
    ) -> None:
        if self.id is None:
            from app.db.ids import uuid7

            self.id = uuid7()
        p = crypto.Purpose.CREDENTIAL
        self.username_enc = crypto.encrypt(username.encode(), p, self._aad("username"), keyring)
        self.password_enc = crypto.encrypt(password.encode(), p, self._aad("password"), keyring)

    def clear_credentials(self) -> None:
        self.username_enc = None
        self.password_enc = None

    def get_credentials(self, keyring: crypto.Keyring | None = None) -> tuple[str, str] | None:
        if self.username_enc is None or self.password_enc is None:
            return None
        p = crypto.Purpose.CREDENTIAL
        return (
            crypto.decrypt(self.username_enc, p, self._aad("username"), keyring).decode(),
            crypto.decrypt(self.password_enc, p, self._aad("password"), keyring).decode(),
        )

    def __repr__(self) -> str:  # never include credentials
        return f"<Camera id={self.id} name={self.name!r} provider={self.provider}>"
