"""Camera API schemas (API_CONTRACTS §2.6). Credentials are write-only (never in output)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.cameras import USB_DEVICE_PATTERN
from app.models.enums import (
    CameraDirection,
    CameraProvider,
    CameraQualityStatus,
    CameraStatus,
    CameraVendor,
)
from app.schemas.common import StrictModel

HOST_RE = re.compile(r"^[A-Za-z0-9.:-]{1,255}$")
MEDIA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class TestSourceSettings(StrictModel):
    """Synthetic frames (pattern) or a replayed file/folder from the camera service's
    TEST_MEDIA_DIR (media). `simulate` injects failures to exercise reconnection."""

    source: Literal["pattern", "media"] = "pattern"
    media: str | None = Field(default=None, max_length=100)
    width: int = Field(default=1280, ge=160, le=3840)
    height: int = Field(default=720, ge=120, le=2160)
    fps: int = Field(default=15, ge=1, le=60)
    simulate: Literal["none", "flaky", "disconnect", "offline", "freeze"] = "none"

    @model_validator(mode="after")
    def _media(self) -> TestSourceSettings:
        if self.source == "media":
            if not self.media or not MEDIA_RE.match(self.media) or ".." in self.media:
                raise ValueError("media must be a file or folder name inside TEST_MEDIA_DIR")
        else:
            self.media = None
        return self


class CameraSettings(StrictModel):
    sample_fps: int = Field(default=5, ge=1, le=30)  # frames per second handed to processing
    min_face_px: int = Field(default=80, ge=40, le=400)  # quality-test face size target
    transport: Literal["tcp", "udp"] = "tcp"  # RTSP transport
    test: TestSourceSettings | None = None


Point = tuple[float, float]


def _check_roi(v: list[Point] | None) -> list[Point] | None:
    if v is None:
        return None
    if not 3 <= len(v) <= 20:
        raise ValueError("roi needs 3-20 points")
    for x, y in v:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("roi points are relative coordinates in [0, 1]")
    return v


def _check_path(v: str | None) -> str | None:
    if v is None:
        return None
    if not v.startswith("/") or "@" in v or "://" in v or any(c.isspace() for c in v):
        raise ValueError("stream path must start with / and contain no credentials or spaces")
    return v


class _CameraFields(StrictModel):
    location: str | None = Field(default=None, max_length=200)
    vendor: CameraVendor | None = None
    model: str | None = Field(default=None, max_length=100)
    host: str | None = Field(default=None, max_length=255)
    rtsp_port: int | None = Field(default=None, ge=1, le=65535)
    onvif_port: int | None = Field(default=None, ge=1, le=65535)
    main_stream_path: str | None = Field(default=None, max_length=512)
    sub_stream_path: str | None = Field(default=None, max_length=512)
    device: str | None = Field(default=None, max_length=32)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    direction: CameraDirection | None = None
    recognition_enabled: bool | None = None
    roi: list[Point] | None = None
    settings: CameraSettings | None = None
    # Departments served by this camera (empty list = all employees)
    department_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    # Branch whose employees this camera records (default: the selected / main branch).
    branch_id: uuid.UUID | None = None

    @field_validator("roi")
    @classmethod
    def _roi(cls, v: list[Point] | None) -> list[Point] | None:
        return _check_roi(v)

    @field_validator("main_stream_path", "sub_stream_path")
    @classmethod
    def _paths(cls, v: str | None) -> str | None:
        return _check_path(v)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        if v is not None and not HOST_RE.match(v):
            raise ValueError("host must be an IP address or host name")
        return v

    @field_validator("device")
    @classmethod
    def _device(cls, v: str | None) -> str | None:
        if v is not None and not re.match(USB_DEVICE_PATTERN, v):
            raise ValueError("device must be a camera index (0-99) or /dev/videoN")
        return v


class CameraCreate(_CameraFields):
    name: str = Field(min_length=1, max_length=120)
    provider: CameraProvider = CameraProvider.RTSP

    @model_validator(mode="after")
    def _by_provider(self) -> CameraCreate:
        validate_provider_fields(
            self.provider,
            self.host,
            self.main_stream_path,
            self.device,
            self.username,
            self.password,
        )
        return self


class CameraUpdate(_CameraFields):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider: CameraProvider | None = None
    clear_credentials: bool = False


def validate_provider_fields(
    provider: CameraProvider,
    host: str | None,
    main_stream_path: str | None,
    device: str | None,
    username: str | None,
    password: str | None,
    *,
    has_stored_credentials: bool = False,
) -> None:
    """Shared by create and (on the merged state) update."""
    if (username is None) != (password is None):
        raise ValueError("username and password must be given together")
    if provider is CameraProvider.RTSP:
        if not host or not main_stream_path:
            raise ValueError("RTSP cameras need host and main_stream_path")
    elif provider is CameraProvider.USB:
        if not device:
            raise ValueError("USB cameras need device (e.g. 0)")
    if provider is not CameraProvider.RTSP and (username is not None or has_stored_credentials):
        raise ValueError("only RTSP cameras can have credentials")


class Monitoring(BaseModel):
    reported_at: datetime | None
    stale: bool  # no report from the camera service recently: status may be outdated
    runtime: dict[str, Any] | None


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    location: str | None
    provider: CameraProvider
    vendor: CameraVendor
    model: str | None
    host: str | None
    rtsp_port: int
    onvif_port: int | None
    main_stream_path: str | None
    sub_stream_path: str | None
    device: str | None
    has_credentials: bool
    stream_url_redacted: str | None = None
    direction: CameraDirection
    recognition_enabled: bool
    is_enabled: bool
    status: CameraStatus
    monitoring: Monitoring | None = None
    last_seen_at: datetime | None
    last_error: str | None
    quality_status: CameraQualityStatus
    quality_tested_at: datetime | None
    quality_report: dict[str, Any] | None
    roi: list[Any] | None
    settings: dict[str, Any]
    department_ids: list[uuid.UUID] = []
    config_version: int
    created_at: datetime
    updated_at: datetime


class ProbeResult(BaseModel):
    ok: bool
    error_code: str | None = None
    error: str | None = None
    width: int | None = None
    height: int | None = None
    reported_fps: float | None = None
    open_ms: float | None = None
    backend: str | None = None


class QualityTestRequest(StrictModel):
    duration_seconds: int = Field(default=5, ge=2, le=20)


# --- internal (camera service) --------------------------------------------------------------


class HealthIssue(BaseModel):
    """One finding of the camera service health check (extra fields carry the numbers)."""

    model_config = ConfigDict(extra="allow")

    code: str = Field(max_length=32)
    severity: Literal["bad", "warn", "tip"]


class CameraHealth(StrictModel):
    verdict: Literal["good", "warn", "bad"]
    issues: list[HealthIssue] = Field(default_factory=list, max_length=20)


class StatusReport(StrictModel):
    camera_id: uuid.UUID
    config_version: int = Field(ge=1)
    state: Literal["CONNECTING", "ONLINE", "DEGRADED", "OFFLINE", "STOPPED"]
    last_frame_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=2000)
    error_code: str | None = Field(default=None, max_length=64)
    measured_fps: float | None = None
    frames_total: int | None = None
    read_failures: int | None = None
    reconnects: int | None = None
    width: int | None = None
    height: int | None = None
    uptime_seconds: float | None = None
    # automatic health (camera service): codec, per-camera CPU, recent losses, verdict + advice
    codec: str | None = Field(default=None, max_length=16)
    cpu_percent: float | None = None
    failures_5m: int | None = None
    reconnects_1h: int | None = None
    health: CameraHealth | None = None


class StatusBatch(StrictModel):
    reports: list[StatusReport] = Field(max_length=500)
