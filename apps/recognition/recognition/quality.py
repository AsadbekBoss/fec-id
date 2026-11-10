"""Image quality assessment. A face that fails is reported as LOW_QUALITY and is never compared
with the gallery, because identifying from poor evidence is how false matches happen.

Measures (all cheap, explainable, configurable in Settings):
* face width / inter-ocular distance in pixels (resolution at distance),
* detector confidence,
* sharpness: variance of the Laplacian on the aligned 112x112 crop (motion / focus blur),
* brightness: mean luminance of the aligned crop (under / over exposure, backlight),
* head pose proxies from the 5 landmarks: roll (eye-line angle) and yaw (nose offset).
  These are geometric heuristics, not a full 3D pose estimator, but they reliably flag
  strongly turned or tilted faces.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from recognition.types import Detection, QualityReport


@dataclass(frozen=True)
class QualityLimits:
    min_detection_score: float = 0.85
    min_face_width_px: int = 80
    min_interocular_px: float = 28.0
    min_blur_variance: float = 40.0
    min_brightness: float = 40.0
    max_brightness: float = 215.0
    max_abs_yaw_deg: float = 30.0
    max_abs_roll_deg: float = 20.0


def pose_from_landmarks(lm: np.ndarray) -> tuple[float, float]:
    """(yaw_deg, roll_deg) estimates. Sign: positive yaw = nose toward image right."""
    eye_a, eye_b, nose = lm[0], lm[1], lm[2]
    dx, dy = float(eye_b[0] - eye_a[0]), float(eye_b[1] - eye_a[1])
    roll = math.degrees(math.atan2(dy, dx))
    interocular = math.hypot(dx, dy)
    if interocular < 1e-6:
        return 90.0, roll
    # Project the nose onto the eye line: offset from the eye midpoint, relative to half the
    # inter-ocular distance. ~0 frontal, ~±1 near profile.
    mid = (eye_a + eye_b) / 2
    ux, uy = dx / interocular, dy / interocular
    offset = ((nose[0] - mid[0]) * ux + (nose[1] - mid[1]) * uy) / (interocular / 2)
    yaw = math.degrees(math.asin(max(-1.0, min(1.0, offset))))
    return yaw, roll


def blur_variance(aligned_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY)
    # Central region only: the warped crop has black borders that would inflate the variance.
    return float(cv2.Laplacian(gray[16:96, 16:96], cv2.CV_64F).var())


def brightness(aligned_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY)
    return float(gray[16:96, 16:96].mean())


def pre_check(det: Detection, limits: QualityLimits) -> QualityReport:
    """Checks that need no crop (run before alignment)."""
    lm = det.landmarks
    interocular = float(np.linalg.norm(lm[1] - lm[0]))
    yaw, roll = pose_from_landmarks(lm)
    reasons: list[str] = []
    if det.score < limits.min_detection_score:
        reasons.append("low_detection_score")
    if det.box[2] < limits.min_face_width_px:
        reasons.append("face_too_small")
    if interocular < limits.min_interocular_px:
        reasons.append("eyes_too_close_in_pixels")
    if abs(yaw) > limits.max_abs_yaw_deg:
        reasons.append("head_turned")
    if abs(roll) > limits.max_abs_roll_deg:
        reasons.append("head_tilted")
    return QualityReport(
        passed=not reasons, reasons=reasons, face_width_px=det.box[2], interocular_px=interocular,
        detection_score=det.score, yaw_deg=yaw, roll_deg=roll,
    )


def post_check(report: QualityReport, aligned_bgr: np.ndarray, limits: QualityLimits) -> QualityReport:
    """Checks on the aligned crop (sharpness, exposure)."""
    report.blur_variance = blur_variance(aligned_bgr)
    report.brightness = brightness(aligned_bgr)
    if report.blur_variance < limits.min_blur_variance:
        report.reasons.append("blurry")
    if report.brightness < limits.min_brightness:
        report.reasons.append("too_dark")
    if report.brightness > limits.max_brightness:
        report.reasons.append("too_bright")
    report.passed = not report.reasons
    return report
