"""FastAPI surface (technical plan §5.6) with edge hardening (§7).

    POST /chat      → Server-Sent Events: token events + one final structured
                      event carrying product cards and the snapshot id
    GET  /health    → liveness
    GET  /demo      → self-contained demo chat page
    GET  /snapshot  → the catalogue version currently being served

The app is built by ``create_app(config)`` so the whole surface — including the
rate limiter, CORS allowlist, security headers and body-size cap — is bound to
one config and is fully testable. The service (and thus the snapshot) is built
lazily on first use and cached on ``app.state``. Backends are chosen entirely by
environment (see ``chat.config``).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chat.config import Config, load_config
from chat.ratelimit import RateLimiter
from chat.service import ChatService, build_service

logger = logging.getLogger("wine_agent.chat")

_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
_IMG_DIR = os.path.join(_REPO_ROOT, "img")
_SNAPSHOT_UNAVAILABLE = "Catalogue snapshot is not available yet. Please try again shortly."

# Applied to every response. CSP allows the demo page's inline style/script and
# same-origin XHR; images may load over https (placeholder host / shop CDN); it
# forbids framing (clickjacking) and external code.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = Field(default=None)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _client_key(request: Request, config: Config) -> str:
    if config.trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    limiter = RateLimiter(config.rate_limit_max, config.rate_limit_window_seconds)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "wine-agent starting — llm_backend=%s embed_backend=%s",
            config.llm_backend,
            config.embed_backend,
        )
        yield

    app = FastAPI(
        title="Wine Agent — chat", docs_url=None, redoc_url=None, lifespan=_lifespan
    )

    if os.path.isdir(_IMG_DIR):
        # Sample bottle photos for the curated card slots (chat.images) —
        # optional: falls back to generated placeholders if absent.
        app.mount("/img", StaticFiles(directory=_IMG_DIR), name="img")

    # CORS: only the shop's own origin(s) may call the API from a browser.
    # Empty allowlist ⇒ no CORS headers ⇒ browsers block cross-origin reads.
    if config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.allowed_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def _body_cap_and_headers(request: Request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > config.max_body_bytes:
                return JSONResponse(
                    status_code=413, content={"error": "Request body too large."}
                )
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def get_service() -> ChatService:
        service = getattr(app.state, "service", None)
        if service is None:
            service = build_service(config)
            app.state.service = service
        return service

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
            service = get_service()
            ref = service.snapshot_ref()
        except FileNotFoundError:
            logger.exception("snapshot unavailable")
            return JSONResponse(status_code=503, content={"error": _SNAPSHOT_UNAVAILABLE})
        payload = ref.model_dump(mode="json")
        payload["backend"] = service.config.llm_backend
        return JSONResponse(content=payload)

    @app.post("/chat")
    def chat(req: ChatRequest, request: Request):
        decision = limiter.check(_client_key(request, config))
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(int(decision.retry_after) + 1)},
                content={"error": "Too many requests — please slow down."},
            )

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

    return app


app = create_app()
