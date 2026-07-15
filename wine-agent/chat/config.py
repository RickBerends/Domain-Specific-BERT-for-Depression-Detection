"""Runtime configuration, env-driven.

The two backend selectors default to ``fake`` so a fresh checkout runs and its
tests pass with zero external services (the "runnable offline" requirement).
Set ``WINE_LLM_BACKEND=ollama`` / ``WINE_EMBED_BACKEND=ollama`` to use a local
Ollama, per technical plan §5.4, or ``WINE_LLM_BACKEND=groq`` for Groq's free,
fast, OpenAI-compatible hosted inference (no local hardware needed). Nothing
else in the code changes — that is the point of the adapter seam.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def require_secret(name: str) -> str:
    """Fetch a required secret from the environment.

    Sanctioned accessor for anything sensitive: raises if unset rather than
    falling back to a baked-in default, and (unlike a plain ``os.getenv`` at
    call sites) keeps secret handling in one auditable place. The value is
    returned but never logged here.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required secret {name!r}. Set it in the environment "
            "(see .env.example); never hard-code or commit it."
        )
    return value


@dataclass(frozen=True)
class Config:
    llm_backend: str = os.getenv("WINE_LLM_BACKEND", "fake")  # fake | ollama | groq
    embed_backend: str = os.getenv("WINE_EMBED_BACKEND", "fake")  # fake | ollama

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_llm_model: str = os.getenv("WINE_LLM_MODEL", "qwen2.5:7b-instruct")
    ollama_embed_model: str = os.getenv("WINE_EMBED_MODEL", "bge-m3")
    # Per-chunk read timeout for streamed Ollama responses. CPU-only inference
    # can leave long silent gaps mid prompt-eval before the first token —
    # bump this (WINE_OLLAMA_READ_TIMEOUT) on slower hardware.
    ollama_read_timeout: float = float(os.getenv("WINE_OLLAMA_READ_TIMEOUT", "180"))

    # Free tier: console.groq.com. 8B default — plenty for this task's short,
    # grounded generations, and a much higher free daily request allowance
    # than the 70B tier (WINE_GROQ_MODEL="llama-3.3-70b-versatile" to upgrade
    # quality at the cost of a tighter rate limit).
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("WINE_GROQ_MODEL", "llama-3.1-8b-instant")
    groq_read_timeout: float = float(os.getenv("WINE_GROQ_READ_TIMEOUT", "30"))

    top_k: int = int(os.getenv("WINE_TOP_K", "6"))
    # How many of the top_k-ranked candidates become displayed cards. Kept
    # separate from top_k so retrieval/ranking still works over a wider pool
    # even though only a curated, non-scrolling set is shown.
    card_limit: int = int(os.getenv("WINE_CARD_LIMIT", "3"))
    max_message_chars: int = int(os.getenv("WINE_MAX_MSG_CHARS", "2000"))
    history_turns: int = int(os.getenv("WINE_HISTORY_TURNS", "4"))
    session_ttl_seconds: float = float(os.getenv("WINE_SESSION_TTL", "1800"))

    # --- Edge hardening (technical plan §7) ---
    rate_limit_max: int = int(os.getenv("WINE_RATE_LIMIT_MAX", "20"))
    rate_limit_window_seconds: float = float(os.getenv("WINE_RATE_LIMIT_WINDOW", "600"))
    max_body_bytes: int = int(os.getenv("WINE_MAX_BODY_BYTES", "16384"))
    # CORS allowlist for the embed widget. Empty ⇒ same-origin only.
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: _split_csv(os.getenv("WINE_ALLOWED_ORIGINS", ""))
    )
    # Trust X-Forwarded-For for the client IP (only enable behind a known proxy).
    trust_proxy: bool = os.getenv("WINE_TRUST_PROXY", "").lower() in ("1", "true", "yes")

    # Base URL for generated placeholder card images (chat.images) — used only
    # when a product has no real image_url. placehold.co is a purpose-built
    # placeholder-image generator, not a stand-in for real photography.
    placeholder_image_base_url: str = os.getenv(
        "WINE_PLACEHOLDER_IMAGE_BASE_URL", "https://placehold.co"
    )

    # Path to the published snapshot (SQLite catalog + serialized vector index).
    snapshot_dir: str = os.getenv(
        "WINE_SNAPSHOT_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "snapshot"),
    )


def load_config() -> Config:
    return Config()
