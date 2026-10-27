"""A small sliding-window rate limiter keyed by client.

In-memory on purpose: simple to read and fine for one replica. With several
replicas each pod keeps its own window, so move this to Redis if you scale out.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """Record a hit. Returns (allowed, seconds_until_retry)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, round(self.window - (now - hits[0]), 1)
            hits.append(now)
            if len(self._hits) > 10_000:  # keep memory bounded
                self._evict(now)
            return True, 0.0

    def _evict(self, now: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window]
        for key in stale:
            del self._hits[key]
