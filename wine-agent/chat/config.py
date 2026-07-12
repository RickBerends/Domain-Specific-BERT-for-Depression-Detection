"""Runtime configuration, env-driven.

The two backend selectors default to ``fake`` so a fresh checkout runs and its
tests pass with zero external services (the "runnable offline" requirement).
Set ``WINE_LLM_BACKEND=ollama`` / ``WINE_EMBED_BACKEND=ollama`` to use a local
Ollama, per technical plan §5.4. Nothing else in the code changes — that is the
point of the adapter seam.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    llm_backend: str = os.getenv("WINE_LLM_BACKEND", "fake")  # fake | ollama
    embed_backend: str = os.getenv("WINE_EMBED_BACKEND", "fake")  # fake | ollama

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_llm_model: str = os.getenv("WINE_LLM_MODEL", "qwen2.5:7b-instruct")
    ollama_embed_model: str = os.getenv("WINE_EMBED_MODEL", "bge-m3")

    top_k: int = int(os.getenv("WINE_TOP_K", "6"))
    max_message_chars: int = int(os.getenv("WINE_MAX_MSG_CHARS", "2000"))
    history_turns: int = int(os.getenv("WINE_HISTORY_TURNS", "4"))
    session_ttl_seconds: float = float(os.getenv("WINE_SESSION_TTL", "1800"))

    # Path to the published snapshot (SQLite catalog + serialized vector index).
    snapshot_dir: str = os.getenv(
        "WINE_SNAPSHOT_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "snapshot"),
    )


def load_config() -> Config:
    return Config()
