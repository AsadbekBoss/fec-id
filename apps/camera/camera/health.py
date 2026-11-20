"""Automatic camera health: a verdict and concrete advice from what the worker measures.

The point: an administrator adds a camera and the system says whether it is fit for attendance
and what to change on the camera (codec, resolution, frame rate, network) without anyone
having to watch the picture. Pure function, no I/O.

Severities: "bad" (not working), "warn" (works, but unreliable or too heavy), "tip" (fine, could be
lighter). The verdict is the worst of bad/warn; tips never lower it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FULL_HD = 1920 * 1080
CPU_HIGH = 70.0  # % of one core used by this camera's worker thread
CPU_NOTICEABLE = 40.0
FRAME_LOSS_5M = 10
RECONNECTS_1H = 3
LOW_FPS = 5.0
HIGH_FPS = 20.0
MIN_RTSP_WIDTH = 1280  # below this faces at the door are usually too small


def codec_name(fourcc: str | None) -> str | None:
    if not fourcc:
        return None
    f = fourcc.strip().lower()
    if f in {"hevc", "h265", "hev1", "hvc1", "x265"}:
        return "H.265"
    if f in {"h264", "avc1", "x264", "avc"}:
        return "H.264"
    if f in {"mjpg", "mjpeg"}:
        return "MJPEG"
    return f.upper() or None


@dataclass(frozen=True)
class Measurements:
    provider: str
    state: str
    uptime_seconds: float | None
    width: int | None
    height: int | None
    codec: str | None
    measured_fps: float | None
    cpu_percent: float | None
    failures_5m: int
    reconnects_1h: int


def assess(m: Measurements) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def add(code: str, severity: str, **data: Any) -> None:
        issues.append({"code": code, "severity": severity, **data})

    if m.state in ("OFFLINE", "CONNECTING", "STOPPED"):
        add("offline", "bad")
        return {"verdict": "bad", "issues": issues}

    settled = (m.uptime_seconds or 0) >= 30  # judge rates only after the stream settled
    heavy = m.cpu_percent is not None and m.cpu_percent >= CPU_NOTICEABLE
    if m.reconnects_1h >= RECONNECTS_1H:
        add("unstable_connection", "warn", count=m.reconnects_1h)
    if m.failures_5m >= FRAME_LOSS_5M:
        add("frame_loss", "warn", count=m.failures_5m)
    if settled and m.measured_fps is not None and m.measured_fps < LOW_FPS:
        add("low_fps", "warn", fps=round(m.measured_fps, 1))
    if m.cpu_percent is not None and m.cpu_percent >= CPU_HIGH:
        add("cpu_high", "warn", cpu=round(m.cpu_percent))
    if m.codec == "H.265":
        add("h265", "warn" if heavy else "tip")
    if m.width and m.height and m.width * m.height > FULL_HD * 1.2:
        add("resolution_high", "warn" if heavy else "tip", width=m.width, height=m.height)
    if m.provider == "RTSP" and m.width and m.width < MIN_RTSP_WIDTH:
        add("resolution_low", "tip", width=m.width, height=m.height)
    if settled and m.measured_fps is not None and m.measured_fps > HIGH_FPS:
        add("fps_high", "tip", fps=round(m.measured_fps))

    worst = {i["severity"] for i in issues}
    verdict = "bad" if "bad" in worst else "warn" if "warn" in worst else "good"
    return {"verdict": verdict, "issues": issues}
