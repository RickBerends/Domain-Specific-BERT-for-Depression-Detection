"""API surface: SSE streaming, health, snapshot."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(config):
    # Build a fresh app bound to the test snapshot/config.
    import chat.api as api

    with TestClient(api.create_app(config)) as c:
        yield c


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_demo_page_served(client):
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Wine Agent" in resp.text


def test_snapshot_endpoint(client):
    data = client.get("/snapshot").json()
    assert data["snapshot_id"] == "test"
    assert data["product_count"] == 20


def test_snapshot_exposes_only_metadata(client):
    # /snapshot must leak version metadata only — never catalogue rows.
    data = client.get("/snapshot").json()
    assert set(data) <= {
        "snapshot_id", "created_at", "published", "product_count", "content_count",
    }
    assert "json" not in data and "products" not in data


def test_database_file_not_web_reachable(client):
    # The SQLite catalogue is a local file, never a served route.
    for path in (
        "/catalog.db",
        "/data/snapshot/catalog.db",
        "/snapshot/catalog.db",
        "/vectors.json",
        "/static/../snapshot/catalog.db",
    ):
        assert client.get(path).status_code == 404, path


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
