"""Anonymous visitor ids: one id per person while they stay, forgotten after the TTL."""

import numpy as np

from recognition.visitors import VisitorCache


def unit(seed: int, dim: int = 128) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def near(v: np.ndarray, noise: float, seed: int) -> np.ndarray:
    w = v + noise * np.random.default_rng(seed).standard_normal(v.shape).astype(np.float32) / np.sqrt(v.size)
    return w / np.linalg.norm(w)


def test_same_face_keeps_its_id_and_others_get_new_ones() -> None:
    c = VisitorCache(threshold=0.4)
    a, b = unit(1), unit(2)
    va = c.assign(a, 0)
    assert all(c.assign(near(a, 0.5, s), float(s)) == va for s in range(10))  # pose/light changes
    vb = c.assign(b, 11)
    assert vb != va and len(c) == 2


def test_forgotten_after_ttl_and_size_bounded() -> None:
    c = VisitorCache(threshold=0.4, ttl=60, max_size=3)
    a = unit(1)
    first = c.assign(a, 0)
    assert c.assign(a, 59) == first
    assert c.assign(a, 59 + 61) != first  # not seen for longer than the TTL: a new visitor
    for i in range(10):
        c.assign(unit(100 + i), 200 + i)
    assert len(c) <= 3
