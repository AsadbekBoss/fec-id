"""Stream quality test: is THIS stream suitable for face recognition?

A stream is never assumed suitable. The verdict is:
* FAILED: no frames, too low fps or resolution, frozen, badly exposed or blurry stream, or
  faces were seen but mostly fail the recognition quality gate (e.g. too small at distance).
* INCONCLUSIVE: the stream itself looks fine, but no face could be assessed (the recognition
  service is not configured, or nobody stood in front of the camera). Re-test with a person
  at the planned distance.
* PASSED: stream checks pass AND faces were assessed and mostly pass the quality gate.
  PASSED is necessary, not sufficient: recognition accuracy still needs the evaluation
  protocol (apps/recognition/EVALUATION_PROTOCOL.md).
"""

from __future__ import annotations

import itertools
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import cv2
import numpy as np

MIN_WIDTH, MIN_HEIGHT = 640, 480
MIN_FPS = 3.0
MIN_SHARPNESS = 15.0  # Laplacian variance on a 640-px-wide grey frame
DARK, BRIGHT = 35.0, 225.0
FROZEN_RATIO = 0.9
FACE_PASS_RATE = 0.6
FACE_FRAMES = 5  # frames sent to the recognition service


class FaceAnalyzer(Protocol):
    def analyze(self, jpeg: bytes) -> list[dict[str, Any]]: ...  # recognition /quality faces


@dataclass
class Sample:
    frame: np.ndarray
    t: float


def _gray640(frame: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if g.shape[1] > 640:
        g = cv2.resize(g, (640, int(g.shape[0] * 640 / g.shape[1])), interpolation=cv2.INTER_AREA)
    return g


def stream_metrics(samples: list[Sample], grabs: int, failures: int, duration: float) -> dict[str, Any]:
    if not samples:
        return {"frames": 0, "read_failures": failures}
    grays = [_gray640(s.frame) for s in samples]
    h, w = samples[0].frame.shape[:2]
    frozen = sum(
        1 for a, b in itertools.pairwise(grays) if a.shape == b.shape and float(np.mean(cv2.absdiff(a, b))) < 0.5
    )
    return {
        "frames": len(samples),
        "width": int(w),
        "height": int(h),
        "measured_fps": round(grabs / duration, 2) if duration > 0 else None,
        "read_failures": failures,
        "frozen_ratio": round(frozen / max(1, len(grays) - 1), 3),
        "brightness_mean": round(float(np.mean([g.mean() for g in grays])), 1),
        "sharpness_mean": round(float(np.mean([cv2.Laplacian(g, cv2.CV_64F).var() for g in grays])), 1),
    }


def face_metrics(samples: list[Sample], analyzer: FaceAnalyzer, min_face_px: int) -> dict[str, Any]:
    picks = [samples[int(i)] for i in np.linspace(0, len(samples) - 1, min(FACE_FRAMES, len(samples)))]
    faces: list[dict[str, Any]] = []
    frames_with_face = 0
    for s in picks:
        ok, buf = cv2.imencode(".jpg", s.frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            continue
        found = analyzer.analyze(buf.tobytes())
        frames_with_face += bool(found)
        faces.extend(found)
    widths = [float(f.get("quality", {}).get("face_width_px") or 0) for f in faces]
    reasons: Counter[str] = Counter()
    passed = 0
    for f, wpx in zip(faces, widths, strict=True):
        q = f.get("quality", {})
        r = list(q.get("reasons") or [])
        if wpx < min_face_px and "face_too_small" not in r:
            r.append("face_too_small")
        reasons.update(r)
        passed += bool(q.get("passed")) and not r
    return {
        "analysed_frames": len(picks),
        "frames_with_face": frames_with_face,
        "faces": len(faces),
        "passed": passed,
        "pass_rate": round(passed / len(faces), 3) if faces else None,
        "median_face_width_px": round(statistics.median(widths), 1) if widths else None,
        "min_face_px_target": min_face_px,
        "rejection_reasons": dict(reasons),
    }


def verdict(stream: dict[str, Any], faces: dict[str, Any] | None, expected_fps: float | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not stream.get("frames"):
        return "FAILED", ["no_frames"]
    if stream["width"] < MIN_WIDTH or stream["height"] < MIN_HEIGHT:
        reasons.append("low_resolution")
    fps = stream.get("measured_fps") or 0
    if fps < min(MIN_FPS, 0.5 * (expected_fps or MIN_FPS)):
        reasons.append("low_fps")
    if stream["frames"] >= 5 and stream["frozen_ratio"] >= FROZEN_RATIO:
        reasons.append("frozen_stream")
    if stream["brightness_mean"] < DARK:
        reasons.append("too_dark")
    if stream["brightness_mean"] > BRIGHT:
        reasons.append("too_bright")
    if stream["sharpness_mean"] < MIN_SHARPNESS:
        reasons.append("blurry")
    if faces and faces["faces"]:
        if (faces["pass_rate"] or 0) < FACE_PASS_RATE:
            reasons.append("faces_fail_quality_gate")
    if reasons:
        return "FAILED", reasons
    if not faces or not faces["faces"]:
        return "INCONCLUSIVE", ["no_face_assessed"]
    return "PASSED", []


def run(
    samples: list[Sample],
    grabs: int,
    failures: int,
    duration: float,
    *,
    analyzer: FaceAnalyzer | None,
    analyzer_state: str,
    min_face_px: int,
    expected_fps: float | None,
) -> dict[str, Any]:
    stream = stream_metrics(samples, grabs, failures, duration)
    faces = face_metrics(samples, analyzer, min_face_px) if analyzer and samples else None
    v, reasons = verdict(stream, faces, expected_fps)
    notes = {
        "PASSED": "Faces seen during this test met the quality gate. Recognition accuracy still "
        "requires the evaluation protocol before attendance use.",
        "INCONCLUSIVE": "The stream works, but no face could be assessed. Re-run with a person "
        "standing at the planned distance" + ("" if analyzer else " and configure RECOGNITION_URL") + ".",
        "FAILED": "This stream is not suitable for face recognition as configured.",
    }
    return {
        "verdict": v,
        "reasons": reasons,
        "tested_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(duration, 1),
        "stream": stream,
        "faces": faces,
        "recognition_service": analyzer_state,
        "note": notes[v],
    }


def collect(read: Any, duration: float, max_frames: int = 60) -> tuple[list[Sample], int, int, float]:
    """`read()` -> (grabbed: bool, frame | None). Samples evenly over `duration` seconds."""
    samples: list[Sample] = []
    grabs = failures = 0
    start = time.monotonic()
    every = duration / max_frames
    next_keep = start
    while (now := time.monotonic()) - start < duration:
        grabbed, frame = read(now >= next_keep)
        if not grabbed:
            failures += 1
            time.sleep(0.02)
            continue
        grabs += 1
        if frame is not None:
            samples.append(Sample(frame, now))
            next_keep = now + every
    return samples, grabs, failures, time.monotonic() - start
