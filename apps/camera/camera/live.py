"""Live recognition for cameras with recognition enabled (observation only).

* Takes the latest sampled frame from the camera worker (in memory) about `fps` times per second
  and sends it to the recognition service. Frames that arrive while a request is in flight are
  simply skipped (no queue, no backlog, no storage).
* Keeps a small in-memory ring of OBSERVATIONS (decision, score, face size, quality reasons).
  No images, no embeddings. An employee id is kept ONLY for MATCHED decisions.
* Repeated identical observations within `coalesce_seconds` update one entry (count, last_seen)
  instead of flooding the list.
* Nothing here creates attendance events.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol

import cv2
import httpx
import numpy as np

from camera.worker import CameraWorker

log = logging.getLogger(__name__)


class Recognizer(Protocol):
    def recognize(self, jpeg: bytes) -> dict[str, Any]: ...


class RecognitionClient:
    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url.rstrip("/") + "/internal/v1/recognize", token
        self._http = httpx.Client(timeout=15, headers={"Authorization": f"Bearer {token}"})

    def recognize(self, jpeg: bytes) -> dict[str, Any]:
        r = self._http.post(self.url, files={"file": ("frame.jpg", jpeg, "image/jpeg")})
        r.raise_for_status()
        return dict(r.json())


class ObservationLog:
    """Ring buffer shared by all cameras. Thread-safe. Sequence numbers allow polling."""

    def __init__(self, size: int = 300, coalesce_seconds: float = 3.0) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._seq = 0
        self.coalesce = coalesce_seconds

    def add(self, camera_id: str, face: dict[str, Any], now: float) -> None:
        decision = str(face.get("decision") or "UNKNOWN")
        emp = face.get("employee_id") if decision == "MATCHED" else None
        q = face.get("quality") or {}
        with self._lock:
            for item in reversed(self._items):
                if now - item["_t"] > self.coalesce:
                    break
                if item["camera_id"] == camera_id and item["decision"] == decision and item["employee_id"] == emp:
                    self._seq += 1
                    item.update(
                        seq=self._seq,
                        count=item["count"] + 1,
                        last_seen=_iso(),
                        _t=now,
                        score=face.get("top1_score"),
                        face_width_px=q.get("face_width_px"),
                        quality_reasons=q.get("reasons") or [],
                    )
                    return
            self._seq += 1
            self._items.append(
                {
                    "seq": self._seq,
                    "camera_id": camera_id,
                    "decision": decision,
                    "employee_id": emp,
                    "score": face.get("top1_score"),
                    "face_width_px": q.get("face_width_px"),
                    "quality_reasons": q.get("reasons") or [],
                    "count": 1,
                    "first_seen": _iso(),
                    "last_seen": _iso(),
                    "_t": now,
                }
            )

    def since(self, seq: int, camera_id: str | None = None) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            items = [
                {k: v for k, v in i.items() if not k.startswith("_")}
                for i in self._items
                if i["seq"] > seq and (camera_id is None or i["camera_id"] == camera_id)
            ]
            return sorted(items, key=lambda i: i["seq"]), self._seq


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class LiveRecognizer(threading.Thread):
    def __init__(
        self, worker: CameraWorker, recognizer: Recognizer, log_: ObservationLog, fps: float = 2.0, max_side: int = 1280
    ) -> None:
        super().__init__(name=f"live-{worker.spec.camera_id[:8]}", daemon=True)
        self.worker, self.recognizer, self.log = worker, recognizer, log_
        self.interval, self.max_side = 1.0 / max(0.2, fps), max_side
        self._stop = threading.Event()
        self.state = "starting"
        self.last_error: str | None = None
        self.frames_sent = 0
        self.last_latency_ms: float | None = None

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "camera_id": self.worker.spec.camera_id,
            "recognition": self.state,
            "last_error": self.last_error,
            "frames_sent": self.frames_sent,
            "latency_ms": self.last_latency_ms,
        }

    def _encode(self, frame: np.ndarray) -> bytes:
        h, w = frame.shape[:2]
        if max(h, w) > self.max_side:
            s = self.max_side / max(h, w)
            frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return bytes(buf.tobytes()) if ok else b""

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            frame = self.worker.latest_frame(max_age=2.0)
            if frame is None:
                self.state = "waiting_for_frames"
                continue
            jpeg = self._encode(frame)
            del frame
            if not jpeg:
                continue
            t0 = time.perf_counter()
            try:
                result = self.recognizer.recognize(jpeg)
            except (httpx.HTTPError, ValueError) as e:
                self.state = "error"
                code = f" {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else ""
                self.last_error = f"recognition service: {type(e).__name__}{code}"
                self._stop.wait(2.0)
                continue
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            self.frames_sent += 1
            self.state, self.last_error = "active", None
            now = time.monotonic()
            for face in result.get("faces") or []:
                self.log.add(self.worker.spec.camera_id, face, now)
