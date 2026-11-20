"""One monitoring worker per enabled camera.

* Keeps the stream drained (grab), decodes only `sample_fps` frames (retrieve).
* Holds ONLY the latest decoded frame in memory. Nothing is recorded or written to disk
  (no continuous recording, by design). Frame consumers (later: recognition) subscribe via
  `add_consumer()` and receive sampled frames in memory.
* Detects errors and stalls (no frame for `stall_timeout`), then reconnects with exponential
  backoff and jitter. States: CONNECTING -> ONLINE / DEGRADED -> OFFLINE -> CONNECTING ...
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from camera.health import Measurements, assess, codec_name
from camera.providers.base import ErrorCode, FrameSource, ProviderError, StreamInfo
from camera.spec import SourceSpec, redact

log = logging.getLogger(__name__)
Consumer = Callable[[np.ndarray, float], None]


def max_grab_rate(reported_fps: float | None) -> float:
    """Upper bound for grab() calls per second (protects the CPU from non-blocking drivers)."""
    if reported_fps and 0 < reported_fps <= 240:
        return min(120.0, max(5.0, reported_fps * 1.5))
    return 60.0


@dataclass
class Backoff:
    initial: float = 1.0
    maximum: float = 60.0
    jitter: float = 0.2
    attempt: int = 0

    def next_delay(self) -> float:
        base = min(self.maximum, self.initial * (2**self.attempt))
        self.attempt += 1
        return float(base * random.uniform(1 - self.jitter, 1 + self.jitter))  # noqa: S311 (not crypto)

    def reset(self) -> None:
        self.attempt = 0


@dataclass
class WorkerStats:
    frames_total: int = 0
    read_failures: int = 0
    reconnects: int = 0
    started_at: float = field(default_factory=time.monotonic)
    connected_at: float | None = None
    last_frame_mono: float | None = None
    last_frame_at: datetime | None = None
    grab_times: deque[float] = field(default_factory=lambda: deque(maxlen=300))
    recent_failures: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    # health windows (never cleared on reconnect)
    failure_times: deque[float] = field(default_factory=lambda: deque(maxlen=5000))
    reconnect_times: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    cpu_percent: float | None = None  # this worker thread, % of one core
    cpu_mark: tuple[float, float] | None = None  # (wall, thread cpu) at the last sample


class CameraWorker(threading.Thread):
    def __init__(
        self,
        spec: SourceSpec,
        factory: Callable[[SourceSpec], FrameSource],
        *,
        stall_timeout: float = 10.0,
        backoff: Backoff | None = None,
    ) -> None:
        super().__init__(name=f"camera-{spec.camera_id[:8]}", daemon=True)
        self.spec, self._factory = spec, factory
        self.stall_timeout = stall_timeout
        self.backoff = backoff or Backoff()
        self.state = "CONNECTING"
        self.error_code: str | None = None
        self.last_error: str | None = None
        self.info = StreamInfo()
        self.stats = WorkerStats()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: tuple[np.ndarray, float] | None = None
        self._consumers: list[Consumer] = []

    # --- public API ---------------------------------------------------------------------------
    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
        self.state = "STOPPED"

    def latest_with_time(self, max_age: float = 5.0) -> tuple[np.ndarray, float] | None:
        """The latest frame (not copied: read-only use) and its monotonic capture time."""
        with self._lock:
            if self._latest is None or time.monotonic() - self._latest[1] > max_age:
                return None
            return self._latest

    def latest_frame(self, max_age: float = 5.0) -> np.ndarray | None:
        with self._lock:
            if self._latest is None or time.monotonic() - self._latest[1] > max_age:
                return None
            return self._latest[0].copy()

    def add_consumer(self, fn: Consumer) -> None:
        with self._lock:
            self._consumers.append(fn)

    def remove_consumer(self, fn: Consumer) -> None:
        with self._lock:
            if fn in self._consumers:
                self._consumers.remove(fn)

    def measured_fps(self) -> float | None:
        times = list(self.stats.grab_times)
        if len(times) < 2:
            return None
        window = [t for t in times if t >= times[-1] - 5.0]
        if len(window) < 2 or window[-1] == window[0]:
            return None
        return (len(window) - 1) / (window[-1] - window[0])

    def status(self) -> dict[str, Any]:
        st = self.stats
        now = time.monotonic()
        fps = self.measured_fps()
        return {
            "camera_id": self.spec.camera_id,
            "config_version": self.spec.config_version,
            "state": self.state,
            "last_frame_at": st.last_frame_at.isoformat() if st.last_frame_at else None,
            "last_error": self.last_error,
            "error_code": self.error_code,
            "measured_fps": round(fps, 2) if fps else None,
            "frames_total": st.frames_total,
            "read_failures": st.read_failures,
            "reconnects": st.reconnects,
            "width": self.info.width,
            "height": self.info.height,
            "uptime_seconds": round(now - st.connected_at, 1)
            if st.connected_at and self.state in ("ONLINE", "DEGRADED")
            else None,
            **self.health(now),
        }

    def health(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        st = self.stats
        codec = codec_name(self.info.codec)
        failures_5m = sum(1 for t in list(st.failure_times) if now - t <= 300)
        reconnects_1h = sum(1 for t in list(st.reconnect_times) if now - t <= 3600)
        fps = self.measured_fps()
        uptime = now - st.connected_at if st.connected_at else None
        m = Measurements(
            provider=str(getattr(self.spec.provider, "value", self.spec.provider)),
            state=self.state,
            uptime_seconds=uptime,
            width=self.info.width,
            height=self.info.height,
            codec=codec,
            measured_fps=fps,
            cpu_percent=st.cpu_percent,
            failures_5m=failures_5m,
            reconnects_1h=reconnects_1h,
        )
        return {
            "codec": codec,
            "cpu_percent": st.cpu_percent,
            "failures_5m": failures_5m,
            "reconnects_1h": reconnects_1h,
            "health": assess(m),
        }

    # --- thread -------------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            self.state = "CONNECTING"
            source: FrameSource | None = None
            try:
                source = self._factory(self.spec)
                self.info = source.open()
                self.stats.connected_at = time.monotonic()
                self._read_loop(source)
            except ProviderError as e:
                self._fail(e.code, e.message)
            except Exception as e:  # never let one camera kill the thread silently
                log.exception("camera worker crashed")
                self._fail(ErrorCode.STREAM_LOST, redact(f"unexpected error: {type(e).__name__}"))
            finally:
                if source is not None:
                    try:
                        source.close()
                    except Exception:  # noqa: S110 (closing a broken stream)
                        pass
            if self._stop.is_set():
                break
            self.stats.reconnects += 1
            self.stats.reconnect_times.append(time.monotonic())
            self._stop.wait(self.backoff.next_delay())
        self.state = "STOPPED"

    def _fail(self, code: ErrorCode, message: str) -> None:
        self.state = "OFFLINE"
        self.error_code, self.last_error = code.value, redact(message)
        log.warning("camera %s offline: %s %s", self.spec.camera_id, code.value, self.last_error)

    def _read_loop(self, source: FrameSource) -> None:
        interval = 1.0 / max(1, self.spec.sample_fps)
        # Some drivers (e.g. DirectShow webcams) return from grab() at once without a new frame.
        # Never poll faster than the camera can deliver: 1.5x its reported fps, at most 120/s.
        min_gap = 1.0 / max_grab_rate(self.info.reported_fps)
        prev_grab = 0.0
        next_sample = 0.0
        last_ok = time.monotonic()
        first = True
        while not self._stop.is_set():
            now = time.monotonic()
            if now - prev_grab < min_gap:
                self._stop.wait(min_gap - (now - prev_grab))
                now = time.monotonic()
            prev_grab = now
            if not source.grab():
                self.stats.read_failures += 1
                self.stats.recent_failures.append(now)
                self.stats.failure_times.append(now)
                if now - last_ok > self.stall_timeout:
                    raise ProviderError(ErrorCode.STREAM_LOST, f"no frame for {self.stall_timeout:.0f} s")
                self._stop.wait(0.05)
                self._update_state(now)
                continue
            last_ok = now
            self.stats.grab_times.append(now)
            if now >= next_sample:
                next_sample = now + interval
                frame = source.retrieve()
                if frame is None:
                    self.stats.read_failures += 1
                    self.stats.recent_failures.append(now)
                    self.stats.failure_times.append(now)
                    continue
                self._publish(frame, now)
                if first:
                    first = False
                    self.backoff.reset()
                    self.stats.recent_failures.clear()  # failures of the previous connection no longer apply
                    self.error_code = self.last_error = None
                    if not self.info.width:
                        self.info.height, self.info.width = frame.shape[:2]
            self._update_state(now)

    def _publish(self, frame: np.ndarray, now: float) -> None:
        st = self.stats
        st.frames_total += 1
        st.last_frame_mono, st.last_frame_at = now, datetime.now(UTC)
        with self._lock:
            self._latest = (frame, now)
            consumers = list(self._consumers)
        for fn in consumers:
            try:
                fn(frame, now)
            except Exception:
                log.exception("frame consumer failed")

    def _sample_cpu(self, now: float) -> None:
        """CPU of THIS thread (grab + decode happen here), measured every 5 s."""
        cpu = time.thread_time()
        mark = self.stats.cpu_mark
        if mark is None:
            self.stats.cpu_mark = (now, cpu)
        elif now - mark[0] >= 5.0:
            self.stats.cpu_percent = round(100.0 * (cpu - mark[1]) / (now - mark[0]), 1)
            self.stats.cpu_mark = (now, cpu)

    def _update_state(self, now: float) -> None:
        self._sample_cpu(now)
        if self.stats.frames_total == 0:
            return
        recent = sum(1 for t in self.stats.recent_failures if now - t < 10)
        fps, expected = self.measured_fps(), self.info.reported_fps
        slow = fps is not None and expected and now - (self.stats.connected_at or now) > 5 and fps < 0.5 * expected
        self.state = "DEGRADED" if recent >= 3 or slow else "ONLINE"
