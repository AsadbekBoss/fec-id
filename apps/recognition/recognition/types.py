from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


@dataclass(frozen=True)
class Detection:
    """Face box (x, y, w, h in pixels of the processed image), 5 landmarks
    (right eye, left eye, nose tip, right mouth corner, left mouth corner, from the viewer's
    left to right as in ArcFace/YuNet ordering) and detector confidence."""

    box: tuple[float, float, float, float]
    landmarks: np.ndarray  # shape (5, 2), float32
    score: float


class Decision(StrEnum):
    MATCHED = "MATCHED"  # confident identification of an authorised (enrolled) person
    UNKNOWN = "UNKNOWN"  # good face, nobody in the gallery is similar enough
    AMBIGUOUS = "AMBIGUOUS"  # similar to someone but not confidently: never auto-identified
    LOW_QUALITY = "LOW_QUALITY"  # failed the quality gate: not compared at all


@dataclass
class QualityReport:
    passed: bool
    reasons: list[str]
    face_width_px: float
    interocular_px: float
    detection_score: float
    blur_variance: float | None = None
    brightness: float | None = None
    yaw_deg: float | None = None
    roll_deg: float | None = None


@dataclass
class MatchResult:
    decision: Decision
    employee_id: str | None  # set ONLY for MATCHED
    top1_score: float | None
    top2_score: float | None  # best score of a DIFFERENT employee
    candidate_id: str | None  # internal top-1 candidate (logged for evaluation, never returned
    #                            as an identification unless decision is MATCHED)


@dataclass
class FaceResult:
    detection: Detection
    quality: QualityReport
    match: MatchResult
    timings_ms: dict[str, float] = field(default_factory=dict)
