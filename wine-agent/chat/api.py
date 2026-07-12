"""FastAPI surface (technical plan §5.6).

    POST /chat      → Server-Sent Events: token events + one final structured
                      event carrying product cards and the snapshot id
    GET  /health    → liveness
    GET  /snapshot  → the catalogue version currently being served

The service (and thus the snapshot) is built once, lazily, and cached. Backends
are chosen entirely by environment (see ``chat.config``), so the same app runs
with deterministic fakes or against a local Ollama with no code change.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator, Iterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chat.config import load_config
from chat.service import ChatService, build_service

logger = logging.getLogger("wine_agent.chat")

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
_IMG_DIR = os.path.join(_REPO_ROOT, "img")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_config()
    logger.info(
        "wine-agent starting — llm_backend=%s embed_backend=%s",
        config.llm_backend,
        config.embed_backend,
    )
    yield


app = FastAPI(title="Wine Agent — chat", docs_url=None, redoc_url=None, lifespan=_lifespan)

if os.path.isdir(_IMG_DIR):
    # Sample bottle photos for the curated best-match/best-value/different card
    # slots (chat.images) — optional: falls back to generated placeholders if
    # this directory isn't present (e.g. a fresh checkout without /img).
    app.mount("/img", StaticFiles(directory=_IMG_DIR), name="img")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = Field(default=None)


@lru_cache(maxsize=1)
def get_service() -> ChatService:
    return build_service(load_config())


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/demo")
def demo() -> FileResponse:
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "static", "demo.html"),
        media_type="text/html",
    )


_SNAPSHOT_UNAVAILABLE = "Catalogue snapshot is not available yet. Please try again shortly."


@app.get("/snapshot")
def snapshot() -> JSONResponse:
    try:
        service = get_service()
        ref = service.snapshot_ref()
    except FileNotFoundError:
        logger.exception("snapshot unavailable")
        return JSONResponse(status_code=503, content={"error": _SNAPSHOT_UNAVAILABLE})
    payload = ref.model_dump(mode="json")
    payload["backend"] = service.config.llm_backend
    return JSONResponse(content=payload)


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    session_id = req.session_id or str(uuid.uuid4())

    try:
        service = get_service()
    except FileNotFoundError:
        logger.exception("chat service unavailable — no published snapshot")

        def error_stream() -> Iterator[str]:
            yield _sse({"type": "error", "message": _SNAPSHOT_UNAVAILABLE})
            yield _sse({"type": "done", "snapshot_id": None})

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    def event_stream() -> Iterator[str]:
        yield _sse(
            {
                "type": "session",
                "session_id": session_id,
                "backend": service.config.llm_backend,
            }
        )
        for event in service.stream(session_id, req.message):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
