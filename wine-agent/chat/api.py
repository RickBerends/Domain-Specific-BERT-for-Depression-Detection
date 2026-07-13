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
import os
import uuid
from typing import Iterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from chat.config import Config, load_config
from chat.ratelimit import RateLimiter
from chat.service import ChatService, build_service

# Applied to every response. CSP allows the demo page's inline style/script and
# same-origin XHR; it forbids framing (clickjacking) and external code.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
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
    app = FastAPI(title="Wine Agent — chat", docs_url=None, redoc_url=None)
    limiter = RateLimiter(config.rate_limit_max, config.rate_limit_window_seconds)

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
            ref = get_service().snapshot_ref()
        except FileNotFoundError as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})
        return JSONResponse(content=ref.model_dump(mode="json"))

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

    return app


app = create_app()
