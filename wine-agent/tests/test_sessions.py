"""Session memory: history, filter carry-over, cheaper follow-up, TTL."""

from __future__ import annotations

from schemas import ColorType

from chat.service import ChatService
from chat.sessions import SessionStore


def _run(service: ChatService, session: str, message: str) -> list[dict]:
    return list(service.stream(session, message))


def test_history_recorded_and_bounded(service: ChatService):
    for i in range(6):
        _run(service, "hist", f"question number {i} about wine")
    session = service.sessions.get("hist")
    assert len(session.turns) == service.sessions.max_turns
    assert session.turns[-1].role == "assistant"


def test_color_carries_over_to_followup(service: ChatService):
    _run(service, "carry", "a red wine from France")
    _run(service, "carry", "anything around €10?")
    assert service.sessions.get("carry").last_filters.color_type is ColorType.red


def test_cheaper_followup_caps_price(service: ChatService):
    events = _run(service, "cheap", "a red wine")
    cards = next(e["cards"] for e in events if e["type"] == "cards")
    cheapest = min(c["price_eur"] for c in cards if c["price_eur"] is not None)

    events2 = _run(service, "cheap", "do you have a cheaper one?")
    cards2 = [e for e in events2 if e["type"] == "cards"]
    if cards2:  # relaxation may kick in if nothing is cheaper
        session = service.sessions.get("cheap")
        assert session.last_filters.max_price_cents is not None
        assert session.last_filters.max_price_cents < int(cheapest * 100)


def test_sessions_are_isolated(service: ChatService):
    _run(service, "a1", "red wine from Spain")
    _run(service, "b1", "white wine")
    assert service.sessions.get("a1").last_filters.country == "Spain"
    assert service.sessions.get("b1").last_filters.color_type is ColorType.white


def test_ttl_eviction():
    store = SessionStore(max_turns=4, ttl_seconds=0.0)  # everything expires instantly
    store.record("x", "user", "hello")
    fresh = store.get("x")  # prior session evicted on access
    assert fresh.turns == []
