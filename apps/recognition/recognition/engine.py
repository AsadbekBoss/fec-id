"""Recognition pipeline: decode -> detect -> quality pre-check -> align -> quality post-check ->
embed -> 1:N match -> conservative decision. Every stage is timed."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from recognition import registry
from recognition.align import align
from recognition.config import Settings
from recognition.matcher import Gallery, MatchPolicy, decide
from recognition.metrics import PerfStats
from recognition.models import ArcFaceEmbedder, FaceDetector, FaceEmbedder, SCRFDDetector, SFaceEmbedder, YuNetDetector
from recognition.quality import QualityLimits, post_check, pre_check
from recognition.types import Decision, FaceResult, MatchResult


class ImageRejected(ValueError):
    pass


def decode_image(data: bytes, max_side: int) -> np.ndarray:
    if not data:
        raise ImageRejected("empty image")
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ImageRejected("not a decodable image")
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


@dataclass
class ImageResult:
    faces: list[FaceResult]
    timings_ms: dict[str, float]
    image_size: tuple[int, int]
    # Parallel to `faces`; None for faces that were not embedded (LOW_QUALITY). INTERNAL ONLY:
    # never serialised into API responses or logs.
    embeddings: list[np.ndarray | None] = field(default_factory=list, repr=False)


class RecognitionEngine:
    def __init__(self, detector: FaceDetector, embedder: FaceEmbedder, gallery: Gallery,
                 policy: MatchPolicy, limits: QualityLimits, stats: PerfStats | None = None) -> None:
        if gallery.model_id != embedder.model_id:
            raise ValueError("Gallery and embedder use different models")
        self.detector, self.embedder, self.gallery = detector, embedder, gallery
        self.policy, self.limits = policy, limits
        self.stats = stats or PerfStats()
        # OpenCV DNN models are not safe for concurrent calls: serialise inference.
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, s: Settings) -> RecognitionEngine:
        manifest = registry.load_manifest(s.manifest_path)
        env, research = s.app_env, s.allow_research_models
        dspec, dpath = registry.resolve(s.detector, manifest, s.models_dir, app_env=env, allow_research=research)
        espec, epath = registry.resolve(s.embedder, manifest, s.models_dir, app_env=env, allow_research=research)
        detector: FaceDetector = (
            YuNetDetector(dpath, dspec.model_id, s.detection_score_threshold) if dspec.backend == "opencv"
            else SCRFDDetector(dpath, dspec.model_id, s.detection_score_threshold, threads=s.onnx_threads)
        )
        embedder: FaceEmbedder = (
            SFaceEmbedder(epath, espec.model_id) if espec.backend == "opencv"
            else ArcFaceEmbedder(epath, espec.model_id, espec.embedding_dim or 512, s.onnx_threads)
        )
        threshold = s.match_threshold if s.match_threshold is not None else espec.reference_threshold
        if threshold is None:
            raise ValueError("No match threshold configured and the model has no reference threshold")
        policy = MatchPolicy(threshold=threshold, min_margin=s.min_margin, uncertain_band=s.uncertain_band)
        limits = QualityLimits(
            min_detection_score=s.min_detection_score, min_face_width_px=s.min_face_width_px,
            min_interocular_px=s.min_interocular_px, min_blur_variance=s.min_blur_variance,
            min_brightness=s.min_brightness, max_brightness=s.max_brightness,
            max_abs_yaw_deg=s.max_abs_yaw_deg, max_abs_roll_deg=s.max_abs_roll_deg,
        )
        return cls(detector, embedder, Gallery(espec.model_id, embedder.dim), policy, limits)

    def embed_best_face(self, image: np.ndarray) -> tuple[np.ndarray | None, FaceResult | None, str | None]:
        """For enrollment: exactly one face that passes the quality gate. Returns (vector, face, error)."""
        result = self.process(image, match=False)
        if not result.faces:
            return None, None, "no_face"
        if len(result.faces) > 1:
            return None, None, "multiple_faces"
        face = result.faces[0]
        if not face.quality.passed:
            return None, face, "low_quality:" + ",".join(face.quality.reasons)
        return result.embeddings[0], face, None

    def nearest(self, embedding: np.ndarray) -> tuple[str, float] | None:
        """Most similar enrolled employee (for duplicate checks at enrolment)."""
        ranked = self.gallery.scores(embedding)
        return ranked[0] if ranked else None

    def process(self, image: np.ndarray, match: bool = True) -> ImageResult:
        with self._lock:
            return self._process(image, match)

    def _process(self, image: np.ndarray, match: bool) -> ImageResult:
        t0 = time.perf_counter()
        detections = self.detector.detect(image)
        timings = {"detect": (time.perf_counter() - t0) * 1000}
        faces: list[FaceResult] = []
        embeddings: list[np.ndarray | None] = []
        for det in detections:
            ft: dict[str, float] = {}
            t = time.perf_counter()
            quality = pre_check(det, self.limits)
            aligned = align(image, det.landmarks)
            quality = post_check(quality, aligned, self.limits)
            ft["align_quality"] = (time.perf_counter() - t) * 1000
            if not quality.passed:
                faces.append(FaceResult(det, quality, MatchResult(Decision.LOW_QUALITY, None, None, None, None), ft))
                embeddings.append(None)
                continue
            t = time.perf_counter()
            emb = self.embedder.embed(aligned)
            ft["embed"] = (time.perf_counter() - t) * 1000
            embeddings.append(emb)
            if match:
                t = time.perf_counter()
                m = decide(self.gallery.scores(emb), self.policy)
                ft["match"] = (time.perf_counter() - t) * 1000
            else:
                m = MatchResult(Decision.UNKNOWN, None, None, None, None)
            faces.append(FaceResult(det, quality, m, ft))
        timings["total"] = (time.perf_counter() - t0) * 1000
        if match:
            self.stats.record_image(timings, faces)
        return ImageResult(faces, timings, (image.shape[1], image.shape[0]), embeddings)
