"""Recognition performance logging.

* One structured JSON log line per processed face: decision, scores (rounded), quality reasons,
  face size and per-stage latency. NEVER embeddings, images or names. Candidate ids appear only
  for MATCHED faces (an uncertain observation must not be attributed to a person).
* In-process aggregates (counts, latency percentiles over a sliding window) for /stats and
  Prometheus-format /metrics.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections import Counter, deque
from datetime import UTC, datetime

import numpy as np

from recognition.types import Decision, FaceResult

log = logging.getLogger("recognition.perf")
WINDOW = 2000


def configure_logging(level: str) -> None:
    class JsonFormatter(logging.Formatter):
        def format(self, r: logging.LogRecord) -> str:
            payload = {"ts": datetime.fromtimestamp(r.created, UTC).isoformat(), "level": r.levelname,
                       "logger": r.name, "msg": r.getMessage()}
            extra = getattr(r, "data", None)
            if extra:
                payload["data"] = extra
            return json.dumps(payload, ensure_ascii=False)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


class PerfStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.decisions: Counter[str] = Counter()
        self.quality_reasons: Counter[str] = Counter()
        self.images = 0
        self.faces = 0
        self._lat: dict[str, deque[float]] = {}

    def record_image(self, timings_ms: dict[str, float], faces: list[FaceResult]) -> None:
        with self._lock:
            self.images += 1
            self.faces += len(faces)
            for stage, ms in timings_ms.items():
                self._lat.setdefault(stage, deque(maxlen=WINDOW)).append(ms)
            for f in faces:
                self.decisions[f.match.decision.value] += 1
                self.quality_reasons.update(f.quality.reasons)
                for stage, ms in f.timings_ms.items():
                    self._lat.setdefault(stage, deque(maxlen=WINDOW)).append(ms)
        for f in faces:
            log.info(
                "face",
                extra={"data": {
                    "decision": f.match.decision.value,
                    "employee_id": f.match.employee_id if f.match.decision is Decision.MATCHED else None,
                    "top1": None if f.match.top1_score is None else round(f.match.top1_score, 3),
                    "top2": None if f.match.top2_score is None else round(f.match.top2_score, 3),
                    "face_width_px": round(f.quality.face_width_px, 1),
                    "quality_reasons": f.quality.reasons,
                    "timings_ms": {k: round(v, 2) for k, v in f.timings_ms.items()},
                }},
            )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            lat = {
                stage: {
                    "count": len(v),
                    "p50": round(float(np.percentile(v, 50)), 2),
                    "p95": round(float(np.percentile(v, 95)), 2),
                    "p99": round(float(np.percentile(v, 99)), 2),
                    "max": round(max(v), 2),
                }
                for stage, v in self._lat.items() if v
            }
            return {"images": self.images, "faces": self.faces, "decisions": dict(self.decisions),
                    "quality_rejections": dict(self.quality_reasons), "latency_ms": lat}

    def prometheus(self) -> str:
        s = self.snapshot()
        lines = [
            "# TYPE recognition_images_total counter", f"recognition_images_total {s['images']}",
            "# TYPE recognition_faces_total counter", f"recognition_faces_total {s['faces']}",
            "# TYPE recognition_decisions_total counter",
        ]
        lines += [f'recognition_decisions_total{{decision="{k}"}} {v}' for k, v in s["decisions"].items()]  # type: ignore[attr-defined]
        lines.append("# TYPE recognition_latency_ms summary")
        for stage, q in s["latency_ms"].items():  # type: ignore[attr-defined]
            for p in ("p50", "p95", "p99"):
                lines.append(f'recognition_latency_ms{{stage="{stage}",quantile="0.{p[1:]}"}} {q[p]}')
        return "\n".join(lines) + "\n"
