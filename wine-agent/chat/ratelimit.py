"""Per-client rate limiting (technical plan §7 — abuse / token-burning / DoS).

A sliding-window counter keyed by client. In-memory and thread-safe, which fits
the single-VPS deployment (§9); a shared Redis backend slots in behind the same
``check`` interface if the service is ever horizontally scaled. Memory is
bounded by evicting idle keys, so a flood of distinct IPs can't grow it without
limit.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: float  # seconds until the next request would be allowed


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max = max_events
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._checks_since_evict = 0

    def check(self, key: str, now: float | None = None) -> RateLimitResult:
        now = time.monotonic() if now is None else now
        cutoff = now - self.window
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max:
                return RateLimitResult(False, max(0.0, hits[0] + self.window - now))
            hits.append(now)
            self._maybe_evict(now)
            return RateLimitResult(True, 0.0)

    def _maybe_evict(self, now: float) -> None:
        # amortized cleanup of keys whose window has fully aged out
        self._checks_since_evict += 1
        if self._checks_since_evict < 1024:
            return
        self._checks_since_evict = 0
        cutoff = now - self.window
        stale = [k for k, dq in self._hits.items() if not dq or dq[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
