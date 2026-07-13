"""Edge hardening: rate limiting, security headers, CORS, body-size cap."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

import chat.api as api
from chat.ratelimit import RateLimiter


# --- rate limiter unit ---

def test_ratelimiter_allows_then_blocks():
    rl = RateLimiter(max_events=3, window_seconds=60)
    assert [rl.check("ip", now=t).allowed for t in (0, 1, 2)] == [True, True, True]
    blocked = rl.check("ip", now=3)
    assert blocked.allowed is False
    assert blocked.retry_after > 0


def test_ratelimiter_window_slides():
    rl = RateLimiter(max_events=1, window_seconds=10)
    assert rl.check("ip", now=0).allowed is True
    assert rl.check("ip", now=5).allowed is False
    assert rl.check("ip", now=11).allowed is True  # first hit aged out


def test_ratelimiter_keys_are_independent():
    rl = RateLimiter(max_events=1, window_seconds=60)
    assert rl.check("a", now=0).allowed is True
    assert rl.check("b", now=0).allowed is True
    assert rl.check("a", now=1).allowed is False


# --- API integration ---

def _client(config, **overrides):
    cfg = dataclasses.replace(config, **overrides)
    return TestClient(api.create_app(cfg))


def test_chat_rate_limited_returns_429(config):
    client = _client(config, rate_limit_max=2, rate_limit_window_seconds=600)
    ok1 = client.post("/chat", json={"message": "a red wine"})
    ok2 = client.post("/chat", json={"message": "a white wine"})
    blocked = client.post("/chat", json={"message": "a rose"})
    assert ok1.status_code == 200 and ok2.status_code == 200
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_security_headers_present(config):
    client = _client(config)
    for path in ("/health", "/demo"):
        headers = client.get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in headers
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_body_size_cap_returns_413(config):
    client = _client(config, max_body_bytes=500)
    resp = client.post("/chat", json={"message": "x" * 2000})
    assert resp.status_code == 413


def test_no_cors_header_when_allowlist_empty(config):
    client = _client(config)  # allowed_origins defaults to empty
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_cors_header_for_allowed_origin(config):
    client = _client(config, allowed_origins=("https://shop.example",))
    resp = client.get("/health", headers={"Origin": "https://shop.example"})
    assert resp.headers.get("access-control-allow-origin") == "https://shop.example"


def test_cors_preflight_rejects_unknown_origin(config):
    client = _client(config, allowed_origins=("https://shop.example",))
    resp = client.options(
        "/chat",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette's CORS middleware omits the allow-origin header for disallowed origins
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"
