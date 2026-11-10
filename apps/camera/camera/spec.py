"""Stream description received from the Core API (app/services/camera_service.py::source_spec).

The password is a SecretStr: it never appears in repr(), logs or error messages.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

Provider = Literal["RTSP", "USB", "TEST"]


class RtspSpec(BaseModel):
    host: str = Field(pattern=r"^[A-Za-z0-9.:-]{1,255}$")
    port: int = Field(default=554, ge=1, le=65535)
    path: str = Field(min_length=1, max_length=512)
    transport: Literal["tcp", "udp"] = "tcp"
    username: str | None = None
    password: SecretStr | None = None


class UsbSpec(BaseModel):
    device: str = Field(pattern=r"^([0-9]{1,2}|/dev/video[0-9]{1,2})$")


class TestSpec(BaseModel):
    __test__ = False  # not a pytest test class
    source: Literal["pattern", "media"] = "pattern"
    media: str | None = None
    width: int = Field(default=1280, ge=16, le=3840)
    height: int = Field(default=720, ge=16, le=2160)
    fps: int = Field(default=15, ge=1, le=240)
    simulate: Literal["none", "flaky", "disconnect", "offline", "freeze"] = "none"


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    camera_id: str
    config_version: int = 1
    name: str = ""
    provider: Provider
    direction: str = "NONE"
    recognition_enabled: bool = False  # live recognition (only after a PASSED quality test)
    sample_fps: int = Field(default=5, ge=1, le=30)
    min_face_px: int = Field(default=80, ge=16, le=1000)
    rtsp: RtspSpec | None = None
    usb: UsbSpec | None = None
    test: TestSpec | None = None


_URL_CREDENTIALS = re.compile(r"(rtsp[s]?://)[^/@\s]+@", re.IGNORECASE)


def redact(text: str) -> str:
    """Defensive scrub for any message that might contain a credential-bearing URL."""
    return _URL_CREDENTIALS.sub(r"\1***@", text)
