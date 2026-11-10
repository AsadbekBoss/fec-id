"""Face alignment: similarity transform of 5 landmarks onto the canonical 112x112 template.

The template is the de-facto standard used by ArcFace/InsightFace and SFace. The transform is
the least-squares similarity (rotation + uniform scale + translation, no shear) from Umeyama,
"Least-squares estimation of transformation parameters between two point patterns", 1991.
"""

from __future__ import annotations

import cv2
import numpy as np

TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],  # eye (image left)
        [73.5318, 51.5014],  # eye (image right)
        [56.0252, 71.7366],  # nose tip
        [41.5493, 92.3655],  # mouth corner (image left)
        [70.7299, 92.2041],  # mouth corner (image right)
    ],
    dtype=np.float32,
)
OUTPUT_SIZE = 112


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """2x3 matrix M with dst ≈ M @ [src, 1] (least squares, reflection-free)."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    s_c, d_c = src - mu_s, dst - mu_d
    var_s = (s_c**2).sum() / n
    cov = d_c.T @ s_c / n
    u, sig, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:  # avoid a reflection
        d[-1] = -1
    r = u @ np.diag(d) @ vt
    scale = (sig * d).sum() / var_s if var_s > 0 else 1.0
    t = mu_d - scale * r @ mu_s
    return np.hstack([scale * r, t[:, None]]).astype(np.float32)


def align(image_bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    """112x112 aligned face crop (BGR)."""
    m = similarity_transform(landmarks, TEMPLATE_112)
    return cv2.warpAffine(image_bgr, m, (OUTPUT_SIZE, OUTPUT_SIZE), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
