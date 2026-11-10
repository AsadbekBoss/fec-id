"""Face detectors and embedders behind small interfaces, so the licensed model choice
(decision D2) does not leak into the rest of the service."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from recognition.types import Detection


class FaceDetector(Protocol):
    model_id: str

    def detect(self, image_bgr: np.ndarray) -> list[Detection]: ...


class FaceEmbedder(Protocol):
    model_id: str
    dim: int

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray: ...


def l2_normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)


# --- OpenCV Zoo (commercially usable) ---------------------------------------------------------


class YuNetDetector:
    """OpenCV Zoo YuNet (MIT) via cv2.FaceDetectorYN."""

    def __init__(self, path: Path, model_id: str, score_threshold: float = 0.6, nms: float = 0.3) -> None:
        self.model_id = model_id
        self._det = cv2.FaceDetectorYN.create(str(path), "", (320, 320), score_threshold, nms, 5000)

    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        h, w = image_bgr.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(image_bgr)
        if faces is None:
            return []
        out = []
        for f in faces:
            out.append(Detection(
                box=(float(f[0]), float(f[1]), float(f[2]), float(f[3])),
                landmarks=np.asarray(f[4:14], dtype=np.float32).reshape(5, 2),
                score=float(f[14]),
            ))
        return out


class SFaceEmbedder:
    """OpenCV Zoo SFace (Apache-2.0) via cv2.FaceRecognizerSF. 128-d features."""

    def __init__(self, path: Path, model_id: str) -> None:
        self.model_id = model_id
        self.dim = 128
        self._rec = cv2.FaceRecognizerSF.create(str(path), "")

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        return l2_normalize(np.asarray(self._rec.feature(aligned_bgr), dtype=np.float32).reshape(-1))


# --- InsightFace-format ONNX models (licence-gated by the registry) ---------------------------


def _ort_session(path: Path, threads: int):  # type: ignore[no-untyped-def]
    import onnxruntime as ort

    opts = ort.SessionOptions()
    if threads:
        opts.intra_op_num_threads = threads
    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    return ort.InferenceSession(str(path), sess_options=opts, providers=providers)


class SCRFDDetector:
    """SCRFD (InsightFace) ONNX: anchor-free decoding over strides 8/16/32, 2 anchors/cell."""

    STRIDES = (8, 16, 32)
    NUM_ANCHORS = 2

    def __init__(self, path: Path, model_id: str, score_threshold: float = 0.6, nms: float = 0.4,
                 input_size: int = 640, threads: int = 0) -> None:
        self.model_id = model_id
        self.score_threshold, self.nms, self.size = score_threshold, nms, input_size
        self._sess = _ort_session(path, threads)
        self._input = self._sess.get_inputs()[0].name

    @staticmethod
    def anchor_centers(h: int, w: int, stride: int, num_anchors: int) -> np.ndarray:
        ys, xs = np.mgrid[:h, :w]
        centers = (np.stack([xs, ys], axis=-1).astype(np.float32) * stride).reshape(-1, 2)
        return np.repeat(centers, num_anchors, axis=0)

    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        h, w = image_bgr.shape[:2]
        scale = self.size / max(h, w)
        resized = cv2.resize(image_bgr, (int(w * scale), int(h * scale)))
        canvas = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1 / 128.0, (self.size, self.size), (127.5, 127.5, 127.5), swapRB=True)
        outs = self._sess.run(None, {self._input: blob})
        n = len(self.STRIDES)
        boxes, scores, kpss = [], [], []
        for i, stride in enumerate(self.STRIDES):
            sc = outs[i].reshape(-1)
            bb = outs[i + n].reshape(-1, 4) * stride
            kp = outs[i + 2 * n].reshape(-1, 10) * stride
            centers = self.anchor_centers(self.size // stride, self.size // stride, stride, self.NUM_ANCHORS)
            keep = np.where(sc >= self.score_threshold)[0]
            c = centers[keep]
            b = np.stack([c[:, 0] - bb[keep, 0], c[:, 1] - bb[keep, 1], c[:, 0] + bb[keep, 2], c[:, 1] + bb[keep, 3]], axis=1)
            k = np.concatenate([c[:, None, :] + kp[keep].reshape(-1, 5, 2)], axis=0) if len(keep) else np.zeros((0, 5, 2))
            boxes.append(b)
            scores.append(sc[keep])
            kpss.append(k)
        if not boxes or sum(len(s) for s in scores) == 0:
            return []
        b_all, s_all, k_all = np.vstack(boxes) / scale, np.concatenate(scores), np.vstack(kpss) / scale
        xywh = [[float(x1), float(y1), float(x2 - x1), float(y2 - y1)] for x1, y1, x2, y2 in b_all]
        idx = cv2.dnn.NMSBoxes(xywh, s_all.tolist(), self.score_threshold, self.nms)
        return [
            Detection(box=tuple(xywh[i]), landmarks=k_all[i].astype(np.float32), score=float(s_all[i]))
            for i in np.array(idx).reshape(-1)
        ]


class ArcFaceEmbedder:
    """ArcFace (InsightFace) ONNX on 112x112 aligned crops; (x - 127.5) / 127.5, RGB."""

    def __init__(self, path: Path, model_id: str, dim: int = 512, threads: int = 0) -> None:
        self.model_id, self.dim = model_id, dim
        self._sess = _ort_session(path, threads)
        self._input = self._sess.get_inputs()[0].name

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(aligned_bgr, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        return l2_normalize(np.asarray(self._sess.run(None, {self._input: blob})[0]).reshape(-1))
