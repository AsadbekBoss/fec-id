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
import uuid
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


class EventSink(Protocol):
    """Receives confident recognitions (the Core API decides whether they become attendance)."""

    def report(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def report_review(self, meta: dict[str, Any], face_jpeg: bytes) -> dict[str, Any]: ...


REVIEW_DECISIONS = ("AMBIGUOUS", "UNKNOWN")
REVIEW_COOLDOWN = 60.0  # seconds, per camera and decision (only without a visitor id)
REVIEW_RETRY = 60.0  # a visitor whose crop could not be delivered is retried after this
VISITOR_MEMORY = 2 * 3600.0  # forget sent visitor ids after this (the recognition side forgets in 30 min)


def crop_face(frame: np.ndarray, box: list[float], margin: float = 0.4, min_side: int = 128) -> bytes:
    """Face crop with some context, never the whole frame. JPEG bytes ("" if the box is invalid)."""
    h, w = frame.shape[:2]
    x, y, bw, bh = (float(v) for v in box)
    if bw <= 0 or bh <= 0:
        return b""
    mx, my = bw * margin, bh * margin
    x0, y0 = max(0, int(x - mx)), max(0, int(y - my))
    x1, y1 = min(w, int(x + bw + mx)), min(h, int(y + bh + my))
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return b""
    if min(crop.shape[:2]) < min_side:
        s = min_side / min(crop.shape[:2])
        crop = cv2.resize(crop, (int(crop.shape[1] * s) + 1, int(crop.shape[0] * s) + 1), interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return bytes(buf.tobytes()) if ok else b""


def attendance_summary(resp: dict[str, Any]) -> str:
    """Short outcome shown next to an observation: IN / OUT / an ignore reason / why not sent."""
    if not resp.get("recorded"):
        return str(resp.get("reason") or "not_recorded")
    if resp.get("counted") and resp.get("direction"):
        return str(resp["direction"])
    return str(resp.get("ignore_reason") or "ignored")


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
                    if face.get("attendance"):
                        item["attendance"] = face["attendance"]
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
                    "attendance": face.get("attendance"),
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
        self,
        worker: CameraWorker,
        recognizer: Recognizer,
        log_: ObservationLog,
        fps: float = 2.0,
        max_side: int = 1280,
        sink: EventSink | None = None,
        cooldown: float = 300.0,
    ) -> None:
        super().__init__(name=f"live-{worker.spec.camera_id[:8]}", daemon=True)
        self.worker, self.recognizer, self.log = worker, recognizer, log_
        self.interval, self.max_side = 1.0 / max(0.2, fps), max_side
        self._stop = threading.Event()
        self.state = "starting"
        self.last_error: str | None = None
        self.frames_sent = 0
        self.last_latency_ms: float | None = None
        self.sink, self.cooldown = sink, cooldown
        self._sent: dict[str, float] = {}  # employee id -> monotonic time of the last report
        self._review_sent: dict[str, float] = {}  # decision -> monotonic time of the last review
        self._visitors: dict[str, float] = {}  # visitor id -> when its card was sent
        self._visitor_retry: dict[str, float] = {}  # visitor id -> earliest retry after a failed send

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

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if max(h, w) > self.max_side:
            s = self.max_side / max(h, w)
            frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        return frame

    def _encode(self, frame: np.ndarray) -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return bytes(buf.tobytes()) if ok else b""

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            frame = self.worker.latest_frame(max_age=2.0)
            if frame is None:
                self.state = "waiting_for_frames"
                continue
            small = self._resize(frame)  # the recognition service sees exactly this image
            del frame
            jpeg = self._encode(small)
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
                model_id = str(result.get("model_id") or "unknown")
                if self.sink is not None and face.get("decision") == "MATCHED" and face.get("employee_id"):
                    face["attendance"] = self._report(face, model_id, now)
                elif self.sink is not None and face.get("decision") in REVIEW_DECISIONS:
                    face["attendance"] = self._report_review(face, model_id, small, now)
                self.log.add(self.worker.spec.camera_id, face, now)

    def _report_review(self, face: dict[str, Any], model_id: str, frame: np.ndarray, now: float) -> str | None:
        """Uncertain face -> review queue (crop only).

        With a visitor id from the recognition service: ONE card per person (however long they
        stand there), and every new person gets a card. Without one (older recognition service):
        at most one card per camera and decision per minute."""
        decision = str(face["decision"])
        visitor = face.get("visitor_id")
        if visitor:
            self._visitors = {k: t for k, t in self._visitors.items() if now - t <= VISITOR_MEMORY}
            if visitor in self._visitors or self._visitor_retry.get(visitor, -1e9) > now:
                return None  # this person already has a card (or a failed send is not due yet)
        elif now - self._review_sent.get(decision, -1e9) < REVIEW_COOLDOWN:
            return None
        crop = crop_face(frame, list(face.get("box") or []) or [0, 0, 0, 0])
        if not crop:
            return None
        if visitor:
            self._visitors[visitor] = now
            self._visitor_retry.pop(visitor, None)
        else:
            self._review_sent[decision] = now
        meta = {
            "event_uid": str(uuid.uuid4()),
            "camera_id": self.worker.spec.camera_id,
            "event_time": _iso(),
            "decision": decision,
            "model_id": model_id,
            "similarity": face.get("top1_score"),
            "second_similarity": face.get("top2_score"),
            "quality_score": face.get("detection_score"),
        }
        sink = self.sink
        if sink is None:
            return None
        try:
            resp = sink.report_review(meta, crop)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("review report failed: %s", type(e).__name__)
            if visitor:  # not delivered: forget the "sent" mark and try this person again later
                self._visitors.pop(visitor, None)
                self._visitor_retry[visitor] = now + REVIEW_RETRY
            return "error"
        return "review" if resp.get("stored") else str(resp.get("reason") or "not_stored")

    def _report(self, face: dict[str, Any], model_id: str, now: float) -> str | None:
        """At most one report per employee per camera per `cooldown` seconds."""
        emp = str(face["employee_id"])
        if now - self._sent.get(emp, -1e9) < self.cooldown:
            return None
        self._sent[emp] = now
        payload = {
            "event_uid": str(uuid.uuid4()),
            "camera_id": self.worker.spec.camera_id,
            "employee_id": emp,
            "event_time": _iso(),
            "decision": "MATCHED",
            "model_id": model_id,
            "similarity": face.get("top1_score"),
            "second_similarity": face.get("top2_score"),
            "quality_score": face.get("detection_score"),
        }
        sink = self.sink
        if sink is None:
            return None
        try:
            return attendance_summary(sink.report(payload))
        except (httpx.HTTPError, ValueError) as e:
            log.warning("attendance report failed: %s", type(e).__name__)
            self._sent.pop(emp, None)  # retry on the next sighting
            return "error"
