"""In-memory sliding-window rate limiter: a cheap first line of defence that rejects floods
BEFORE expensive Argon2 hashing (each hash costs ~64 MiB of RAM, so floods could cause memory
exhaustion).

Per-process only. The authoritative limits are DB-backed (per-account lockout and per-IP
failure count) and hold across processes. Swap this for a Valkey-backed limiter when Valkey is
introduced (ARCHITECTURE §8).
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Registers an attempt. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        q = self._hits.get(key)
        if q is None:
            q = deque()
            self._hits[key] = q
            if len(self._hits) > self.max_keys:  # bound memory: evict least recently used
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, int(q[0] + self.window - now) + 1
        q.append(now)
        return True, 0

    def reset(self) -> None:
        self._hits.clear()
