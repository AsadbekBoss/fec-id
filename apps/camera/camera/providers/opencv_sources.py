"""USB webcam and RTSP IP camera providers (OpenCV / FFmpeg).

RTSP credentials are URL-encoded into the capture URL only in memory, right before opening.
The URL is never logged or returned. FFmpeg's own logging is silenced for the same reason.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from typing import Any
from urllib.parse import quote

import cv2
import numpy as np

from camera.providers.base import ErrorCode, ProviderError, StreamInfo
from camera.spec import RtspSpec, UsbSpec

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")  # quiet: FFmpeg may print URLs
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
_FFMPEG_OPTIONS_LOCK = threading.Lock()  # OPENCV_FFMPEG_CAPTURE_OPTIONS is process-global


class _CvSource:
    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None

    def _info(self, backend: str) -> StreamInfo:
        if self._cap is None:
            return StreamInfo(backend=backend)
        w, h = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0) or None
        return StreamInfo(w or None, h or None, fps, backend, _fourcc(self._cap))

    def grab(self) -> bool:
        return bool(self._cap is not None and self._cap.grab())

    def retrieve(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, frame = self._cap.retrieve()
        return np.asarray(frame) if ok else None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class UsbSource(_CvSource):
    def __init__(self, spec: UsbSpec) -> None:
        super().__init__()
        self.spec = spec

    def open(self) -> StreamInfo:
        dev = self.spec.device
        if sys.platform == "win32":
            backend, name = cv2.CAP_DSHOW, "dshow"
        elif sys.platform.startswith("linux"):
            backend, name = cv2.CAP_V4L2, "v4l2"
        else:
            backend, name = cv2.CAP_ANY, "any"
        target: int | str = int(dev) if dev.isdigit() else dev
        cap = cv2.VideoCapture(target, backend)
        if not cap.isOpened():
            cap.release()
            raise ProviderError(ErrorCode.DEVICE_NOT_FOUND, f"USB camera '{dev}' is not available (missing or in use)")
        self._cap = cap
        return self._info(f"usb:{name}")


def rtsp_url(spec: RtspSpec) -> str:
    """Capture URL with URL-encoded credentials. Handle as a secret."""
    auth = ""
    if spec.username is not None and spec.password is not None:
        auth = f"{quote(spec.username, safe='')}:{quote(spec.password.get_secret_value(), safe='')}@"
    return f"rtsp://{auth}{spec.host}:{spec.port}{spec.path}"


def tcp_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fourcc(cap: Any) -> str | None:
    """Codec FOURCC from the capture ("hevc", "h264", "MJPG" ...), None when unknown."""
    try:
        v = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    except (TypeError, ValueError):
        return None
    text = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")
    return text if text.isprintable() and text else None


class RtspSource(_CvSource):
    def __init__(self, spec: RtspSpec, open_timeout_ms: int, read_timeout_ms: int) -> None:
        super().__init__()
        self.spec, self.open_ms, self.read_ms = spec, open_timeout_ms, read_timeout_ms

    def open(self) -> StreamInfo:
        s = self.spec
        if not tcp_reachable(s.host, s.port, min(5.0, self.open_ms / 1000)):
            raise ProviderError(ErrorCode.UNREACHABLE, f"{s.host}:{s.port} does not answer")
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_ms]
        with _FFMPEG_OPTIONS_LOCK:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{s.transport}"
            cap = cv2.VideoCapture(rtsp_url(s), cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            raise ProviderError(
                ErrorCode.OPEN_FAILED,
                f"{s.host}:{s.port} answered but the stream did not open "
                "(check the stream path, credentials and codec)",
            )
        self._cap = cap
        return self._info(f"rtsp:{s.transport}")
