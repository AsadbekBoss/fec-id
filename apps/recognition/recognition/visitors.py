"""Short-lived anonymous ids for faces that were NOT identified (UNKNOWN / AMBIGUOUS).

Purpose: the review queue should get ONE card per person, not one per minute while a stranger
stands at the door. The same face seen again within `ttl` seconds gets the same id ("v17").

Privacy: embeddings stay in this process's memory only, are never returned or written anywhere,
and are forgotten `ttl` seconds after the face was last seen. Only the opaque id leaves.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

DEFAULT_TTL = 30 * 60.0
MAX_VISITORS = 500


@dataclass
class _Visitor:
    vid: str
    vector: np.ndarray  # L2-normalised running mean
    last_seen: float
    seen: int


class VisitorCache:
    def __init__(self, threshold: float, ttl: float = DEFAULT_TTL, max_size: int = MAX_VISITORS):
        self.threshold, self.ttl, self.max_size = threshold, ttl, max_size
        self._items: list[_Visitor] = []
        self._next = 1
        self._lock = threading.Lock()  # process() runs in worker threads

    def __len__(self) -> int:
        return len(self._items)

    def assign(self, vector: np.ndarray, now: float) -> str:
        v = np.asarray(vector, dtype=np.float32).ravel()
        n = float(np.linalg.norm(v))
        if n == 0:
            raise ValueError("zero embedding")
        v = v / n
        with self._lock:
            self._items = [x for x in self._items if now - x.last_seen <= self.ttl]
            if self._items:
                sims = np.stack([x.vector for x in self._items]) @ v
                best = int(np.argmax(sims))
                if float(sims[best]) >= self.threshold:
                    hit = self._items[best]
                    mean = hit.vector * hit.seen + v  # follow slow changes (pose, light)
                    hit.vector = mean / float(np.linalg.norm(mean))
                    hit.seen, hit.last_seen = hit.seen + 1, now
                    return hit.vid
            if len(self._items) >= self.max_size:  # drop the longest-unseen visitor
                self._items.remove(min(self._items, key=lambda x: x.last_seen))
            vid = f"v{self._next}"
            self._next += 1
            self._items.append(_Visitor(vid, v, now, 1))
            return vid
