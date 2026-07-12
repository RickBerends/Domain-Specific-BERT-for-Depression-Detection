"""Embedder abstraction: deterministic fake (default) + Ollama impl.

The fake is a hashing embedder — stable, offline, and good enough for
retrieval *ordering* on a small seed so tests are deterministic. The Ollama
impl calls ``/api/embeddings`` with the multilingual ``bge-m3`` model chosen in
addendum §4. Same interface, so swapping is a config change (§5.4 escape hatch).
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbedder:
    """Hash tokens into a fixed-dim bag-of-words vector, then L2-normalize.

    Shares vocabulary across languages only by literal token overlap, which is
    fine for deterministic unit tests; real cross-lingual retrieval is the
    Ollama/bge-m3 path.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in text.lower().split():
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        return _normalize(vec)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, dim: int = 1024) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=60) as client:
            for text in texts:
                resp = client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                emb = resp.json()["embedding"]
                self.dim = len(emb)
                out.append(_normalize(emb))
        return out


def build_embedder(cfg) -> Embedder:
    if cfg.embed_backend == "ollama":
        return OllamaEmbedder(cfg.ollama_base_url, cfg.ollama_embed_model)
    return FakeEmbedder()
