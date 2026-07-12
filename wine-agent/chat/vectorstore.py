"""Vector index abstraction.

The first slice ships an ``InMemoryVectorIndex`` (pure-Python cosine, JSON-
serializable) as the working default so it runs anywhere with no heavy deps —
at ~20 products a brute-force scan is instant. The technical plan's production
backend is Chroma embedded (§3); it drops in behind this same tiny interface
without touching the retriever. Metadata is carried alongside each vector so
filtered retrieval ("red under €15" → metadata filter, not a vector guess,
§4.5) has a home to grow into.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class VectorHit:
    id: str
    score: float
    metadata: dict[str, Any]


class VectorIndex(Protocol):
    def query(self, embedding: list[float], top_k: int) -> list[VectorHit]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    # Vectors are stored L2-normalized, so dot product == cosine similarity.
    return sum(x * y for x, y in zip(a, b))


class InMemoryVectorIndex:
    def __init__(self) -> None:
        self._ids: list[str] = []
        self._vecs: list[list[float]] = []
        self._meta: list[dict[str, Any]] = []

    def add(self, id: str, embedding: list[float], metadata: dict[str, Any]) -> None:
        self._ids.append(id)
        self._vecs.append(embedding)
        self._meta.append(metadata)

    def query(self, embedding: list[float], top_k: int) -> list[VectorHit]:
        scored = [
            VectorHit(self._ids[i], _cosine(embedding, self._vecs[i]), self._meta[i])
            for i in range(len(self._ids))
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    # --- persistence: an index is part of a published snapshot ---

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"ids": self._ids, "vecs": self._vecs, "meta": self._meta}, f
            )

    @classmethod
    def load(cls, path: str) -> "InMemoryVectorIndex":
        idx = cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        idx._ids, idx._vecs, idx._meta = data["ids"], data["vecs"], data["meta"]
        return idx
