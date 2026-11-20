"""On-demand operations requested by the Core API: probe (connection test), snapshot, quality
test. If a monitoring worker already streams the camera with the same configuration, its frames
are reused instead of opening a second connection (many cameras limit concurrent sessions)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import cv2
import httpx
import numpy as np

from camera import quality
from camera.config import Settings
from camera.providers.base import ErrorCode, FrameSource, ProviderError
from camera.spec import SourceSpec
from camera.supervisor import Supervisor
from camera.worker import CameraWorker


class OperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class RecognitionQualityClient:
    """Calls the recognition service's /internal/v1/quality (no identification)."""

    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url.rstrip("/") + "/internal/v1/quality", token

    def analyze(self, jpeg: bytes) -> list[dict[str, Any]]:
        r = httpx.post(
            self.url,
            files={"file": ("frame.jpg", jpeg, "image/jpeg")},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
        )
        r.raise_for_status()
        return list(r.json().get("faces", []))


def encode_jpeg(frame: np.ndarray, max_side: int, quality_: int) -> bytes:
    h, w = frame.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality_])
    if not ok:
        raise OperationError("ENCODE_FAILED", "frame could not be encoded")
    return bytes(buf.tobytes())


class Operations:
    def __init__(
        self,
        settings: Settings,
        supervisor: Supervisor,
        factory: Callable[[SourceSpec], FrameSource],
        analyzer: quality.FaceAnalyzer | None = None,
    ) -> None:
        self.s, self.sup, self.factory = settings, supervisor, factory
        self.analyzer = analyzer
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_operations)

    def live_worker(self, spec: SourceSpec) -> CameraWorker | None:
        """The running worker for this exact configuration, if it is delivering frames."""
        return self._live_worker(spec)

    def _live_worker(self, spec: SourceSpec) -> CameraWorker | None:
        w = self.sup.get(spec.camera_id)
        if w and w.spec.config_version == spec.config_version and w.state in ("ONLINE", "DEGRADED"):
            return w
        return None

    def _with_source(self, spec: SourceSpec, fn: Callable[[FrameSource, Any], Any]) -> Any:
        if not self._slots.acquire(timeout=30):
            raise OperationError("BUSY", "too many camera operations in progress")
        src: FrameSource | None = None
        try:
            src = self.factory(spec)
            t0 = time.perf_counter()
            info = src.open()
            info_ms = (time.perf_counter() - t0) * 1000
            return fn(src, (info, info_ms))
        except ProviderError as e:
            raise OperationError(e.code.value, e.message) from None
        finally:
            if src is not None:
                src.close()
            self._slots.release()

    @staticmethod
    def _first_frame(src: FrameSource, timeout: float) -> np.ndarray:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if src.grab():
                frame = src.retrieve()
                if frame is not None:
                    return frame
            else:
                time.sleep(0.05)
        raise OperationError(ErrorCode.STREAM_LOST.value, "connected, but no frame arrived")

    # --- operations ---------------------------------------------------------------------------
    def probe(self, spec: SourceSpec) -> dict[str, Any]:
        w = self._live_worker(spec)
        if w is not None:
            return {
                "ok": True,
                "width": w.info.width,
                "height": w.info.height,
                "reported_fps": w.info.reported_fps,
                "backend": w.info.backend + " (monitor)",
            }

        def go(src: FrameSource, meta: Any) -> dict[str, Any]:
            info, open_ms = meta
            frame = self._first_frame(src, self.s.read_timeout_ms / 1000)
            h, wd = frame.shape[:2]
            return {
                "ok": True,
                "width": info.width or wd,
                "height": info.height or h,
                "reported_fps": info.reported_fps,
                "open_ms": round(open_ms, 1),
                "backend": info.backend,
            }

        try:
            return dict(self._with_source(spec, go))
        except OperationError as e:
            return {"ok": False, "error_code": e.code, "error": e.message}

    def snapshot(self, spec: SourceSpec) -> bytes:
        w = self._live_worker(spec)
        frame = w.latest_frame() if w else None
        if frame is None:
            frame = self._with_source(spec, lambda src, _: self._first_frame(src, self.s.read_timeout_ms / 1000))
        if frame is None:
            raise OperationError(ErrorCode.STREAM_LOST.value, "no frame available")
        return encode_jpeg(frame, self.s.snapshot_max_side, self.s.jpeg_quality)

    def quality_test(self, spec: SourceSpec, duration: float) -> dict[str, Any]:
        analyzer, state = self.analyzer, "used" if self.analyzer else "not_configured"
        w = self._live_worker(spec)
        if w is not None:
            samples, grabs, failures, elapsed = self._collect_from_worker(w, duration)
            expected = w.info.reported_fps
        else:

            def go(src: FrameSource, meta: Any) -> Any:
                def read(decode: bool) -> tuple[bool, np.ndarray | None]:
                    if not src.grab():
                        return False, None
                    return True, src.retrieve() if decode else None

                return (*quality.collect(read, duration), meta[0].reported_fps)

            try:
                samples, grabs, failures, elapsed, expected = self._with_source(spec, go)
            except OperationError as e:
                return {
                    **quality.run(
                        [],
                        0,
                        0,
                        0,
                        analyzer=None,
                        analyzer_state=state,
                        min_face_px=spec.min_face_px,
                        expected_fps=None,
                    ),
                    "error_code": e.code,
                    "error": e.message,
                }
        try:
            return quality.run(
                samples,
                grabs,
                failures,
                elapsed,
                analyzer=analyzer,
                analyzer_state=state,
                min_face_px=spec.min_face_px,
                expected_fps=expected,
            )
        except httpx.HTTPError as e:
            report = quality.run(
                samples,
                grabs,
                failures,
                elapsed,
                analyzer=None,
                analyzer_state=f"error: {type(e).__name__}",
                min_face_px=spec.min_face_px,
                expected_fps=expected,
            )
            return report

    @staticmethod
    def _collect_from_worker(w: CameraWorker, duration: float) -> tuple[list[quality.Sample], int, int, float]:
        """Borrow sampled frames from the running monitor instead of opening a second session."""
        samples: list[quality.Sample] = []
        lock = threading.Lock()

        def take(frame: np.ndarray, t: float) -> None:
            with lock:
                if len(samples) < 60:
                    samples.append(quality.Sample(frame.copy(), t))

        failures0 = w.stats.read_failures
        start = time.monotonic()
        w.add_consumer(take)
        try:
            time.sleep(duration)
        finally:
            w.remove_consumer(take)
        elapsed = time.monotonic() - start
        grabs = round((w.measured_fps() or 0.0) * elapsed)
        return samples, grabs, w.stats.read_failures - failures0, elapsed
