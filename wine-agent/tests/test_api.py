"""API surface: SSE streaming, health, snapshot."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(config, monkeypatch):
    # Point the app's cached service at the test snapshot.
    import chat.api as api

    monkeypatch.setattr(api, "load_config", lambda: config)
    api.get_service.cache_clear()
    with TestClient(api.app) as c:
        yield c
    api.get_service.cache_clear()


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_snapshot_endpoint(client):
    data = client.get("/snapshot").json()
    assert data["snapshot_id"] == "test"
    assert data["product_count"] == 20


def test_chat_sse_stream(client):
    resp = client.post("/chat", json={"message": "Do you have a Chianti?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "session"
    assert "cards" in types and "token" in types
    assert types[-1] == "done"


def test_chat_generates_session_id(client):
    resp = client.post("/chat", json={"message": "hello"})
    events = _parse_sse(resp.text)
    session = next(e for e in events if e["type"] == "session")
    assert session["session_id"]
