"""Chat orchestration: event stream, grounding, guards."""

from __future__ import annotations

from chat.service import ChatService


def _collect(service: ChatService, message: str, session="s1") -> list[dict]:
    return list(service.stream(session, message))


def test_stream_emits_cards_tokens_and_done(service: ChatService):
    events = _collect(service, "Do you have a Chianti?")
    types = [e["type"] for e in events]
    assert "cards" in types
    assert "token" in types
    assert types[-1] == "done"
    assert events[-1]["snapshot_id"] == "test"


def test_cards_carry_price_from_metadata(service: ChatService):
    events = _collect(service, "Chianti Classico")
    cards = next(e["cards"] for e in events if e["type"] == "cards")
    chianti = next(c for c in cards if c["slug"] == "villa-antica-chianti-classico-2021")
    assert chianti["price_eur"] == 13.5  # 1350 cents, formatted by the schema


def test_answer_is_grounded_in_context(service: ChatService):
    events = _collect(service, "Tell me about the Chianti")
    text = "".join(e["text"] for e in events if e["type"] == "token")
    # FakeLLM only ever echoes retrieved context, so a real wine name proves grounding.
    assert "Chianti" in text


def test_empty_message_is_guarded(service: ChatService):
    events = _collect(service, "   ")
    assert events[0]["type"] == "error"
    assert not any(e["type"] == "token" for e in events)


def test_overlong_message_is_guarded(service: ChatService):
    events = _collect(service, "x" * 5000)
    assert events[0]["type"] == "error"
