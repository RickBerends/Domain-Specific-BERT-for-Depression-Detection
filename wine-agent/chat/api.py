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
import os
import uuid
from functools import lru_cache
from typing import Iterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from chat.config import load_config
from chat.service import ChatService, build_service

app = FastAPI(title="Wine Agent — chat", docs_url=None, redoc_url=None)


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


@app.get("/snapshot")
def snapshot() -> JSONResponse:
    try:
        ref = get_service().snapshot_ref()
    except FileNotFoundError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    return JSONResponse(content=ref.model_dump(mode="json"))


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    session_id = req.session_id or str(uuid.uuid4())

    try:
        service = get_service()
    except FileNotFoundError as exc:
        def error_stream() -> Iterator[str]:
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done", "snapshot_id": None})

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    def event_stream() -> Iterator[str]:
        yield _sse({"type": "session", "session_id": session_id})
        for event in service.stream(session_id, req.message):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
