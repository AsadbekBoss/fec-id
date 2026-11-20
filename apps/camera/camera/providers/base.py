"""Provider interface. Every camera type implements FrameSource, so providers are replaceable
(see providers/registry.py) without touching the worker, quality test or API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np


class ErrorCode(StrEnum):
    UNREACHABLE = "UNREACHABLE"  # host/port does not answer
    OPEN_FAILED = "OPEN_FAILED"  # reachable, but the stream did not open (path/credentials/codec)
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"  # USB index / device missing or busy
    STREAM_LOST = "STREAM_LOST"  # frames stopped arriving
    CONFIG_INVALID = "CONFIG_INVALID"
    MEDIA_NOT_FOUND = "MEDIA_NOT_FOUND"  # TEST replay file/folder missing
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"  # provider not registered on this host


class ProviderError(Exception):
    """`message` is safe to show and log: it never contains credentials."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


@dataclass
class StreamInfo:
    width: int | None = None
    height: int | None = None
    reported_fps: float | None = None
    backend: str = ""
    codec: str | None = None  # FOURCC as reported by the decoder, e.g. "hevc", "h264"


class FrameSource(Protocol):
    """Lifecycle: open() -> (grab() [retrieve()])* -> close().

    grab() advances the stream (cheap); retrieve() decodes the last grabbed frame. Separating
    them lets the worker keep a live stream drained while decoding only sampled frames."""

    def open(self) -> StreamInfo: ...
    def grab(self) -> bool: ...
    def retrieve(self) -> np.ndarray | None: ...
    def close(self) -> None: ...
