"""Server-side session state (technical plan §5.3).

In-memory, thread-safe, TTL-evicted — the SQLite/Redis upgrade slots in behind
the same ``SessionStore`` interface when persistence matters. A session keeps a
sliding window of turns for the prompt's history block, plus the last applied
filters and the cheapest price shown, which is what makes follow-ups like
"and a cheaper one?" resolvable.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from chat.planner import Filters


@dataclass
class Turn:
    role: str  # user | assistant
    text: str


@dataclass
class Session:
    turns: list[Turn] = field(default_factory=list)
    last_filters: Filters = field(default_factory=Filters)
    last_min_price_cents: int | None = None
    last_seen: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(self, max_turns: int = 8, ttl_seconds: float = 1800.0) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.max_turns = max_turns
        self.ttl = ttl_seconds

    def get(self, session_id: str) -> Session:
        now = time.monotonic()
        with self._lock:
            self._evict(now)
            session = self._sessions.get(session_id)
            if session is None:
                session = Session()
                self._sessions[session_id] = session
            session.last_seen = now
            return session

    def record(self, session_id: str, role: str, text: str) -> None:
        session = self.get(session_id)
        with self._lock:
            session.turns.append(Turn(role=role, text=text))
            del session.turns[: -self.max_turns]

    def _evict(self, now: float) -> None:
        expired = [
            sid for sid, s in self._sessions.items() if now - s.last_seen > self.ttl
        ]
        for sid in expired:
            del self._sessions[sid]
