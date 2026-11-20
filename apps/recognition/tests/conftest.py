from __future__ import annotations

import base64
import os

import numpy as np
import pytest

from recognition.align import TEMPLATE_112
from recognition.crypto import Keyring
from recognition.engine import RecognitionEngine
from recognition.matcher import Gallery, MatchPolicy, Template
from recognition.quality import QualityLimits
from recognition.types import Detection

TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
MODEL_ID = "fake@1"

# Tests never sync the gallery from a real Core API, even if the local .env points to one
# (environment variables take precedence over the .env file).
os.environ["CORE_API_URL"] = ""


@pytest.fixture
def keyring() -> Keyring:
    return Keyring.parse(f"7:{TEST_KEY}", 7)


def frontal_detection(scale: float = 3.0, offset: tuple[float, float] = (100, 80), score: float = 0.95) -> Detection:
    lm = TEMPLATE_112 * scale + np.array(offset, np.float32)
    return Detection((offset[0], offset[1], 112 * scale, 112 * scale), lm.astype(np.float32), score)


def textured_image(h: int = 600, w: int = 600, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(40, 215, (h, w, 3), dtype=np.uint8)


def unit(v: list[float]) -> np.ndarray:
    a = np.asarray(v, np.float32)
    return a / np.linalg.norm(a)


class FakeDetector:
    model_id = "fakedet@1"

    def __init__(self, detections: list[Detection]) -> None:
        self.detections = detections

    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        return list(self.detections)


class FakeEmbedder:
    model_id = MODEL_ID
    dim = 4

    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        assert aligned_bgr.shape == (112, 112, 3)
        return self.vector


def fake_engine(detections: list[Detection], probe: np.ndarray, templates: list[Template] | None = None,
                policy: MatchPolicy | None = None) -> RecognitionEngine:
    gallery = Gallery(MODEL_ID, 4)
    if templates:
        gallery.load(templates)
    return RecognitionEngine(FakeDetector(detections), FakeEmbedder(probe), gallery,
                             policy or MatchPolicy(0.5, 0.05, 0.03), QualityLimits())
