"""Chat orchestration: event stream, grounding, guards."""

from __future__ import annotations

from chat.config import Config
from chat.embeddings import build_embedder
from chat.llm import LLMError
from chat.service import ChatService
from chat.snapshot import SnapshotReader


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


def test_empty_message_guard_is_dutch_for_dutch_input(service: ChatService):
    events = _collect(service, "   ")
    # No language signal in whitespace-only input — guard defaults to English.
    assert "wines" in events[0]["message"]


def test_dutch_question_gets_english_only_tag_from_fake_backend(service: ChatService):
    events = _collect(service, "Welke goedkope rode wijn hebben jullie?")
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text.startswith("[fake-backend, English-only]")


def test_english_question_has_no_fake_backend_tag(service: ChatService):
    events = _collect(service, "Tell me about the Chianti")
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert not text.startswith("[fake-backend, English-only]")


class _BrokenLLM:
    """Simulates an unreachable/erroring Ollama backend."""

    def stream(self, system: str, user: str):
        raise LLMError("boom")


def test_cards_are_curated_with_roles_and_never_exceed_card_limit(service: ChatService):
    events = _collect(service, "a nice red wine")
    cards = next(e["cards"] for e in events if e["type"] == "cards")
    assert len(cards) <= service.card_limit
    assert {c["role"] for c in cards} <= {"best_match", "best_value", "different"}
    assert cards[0]["role"] == "best_match"
    assert all(c["reason"] for c in cards)


def test_cards_always_have_an_image_even_without_real_photo(service: ChatService):
    events = _collect(service, "Chianti Classico")
    cards = next(e["cards"] for e in events if e["type"] == "cards")
    assert all(c["image_url"] for c in cards)


def test_cards_carry_real_product_facts(service: ChatService):
    events = _collect(service, "Chianti Classico")
    cards = next(e["cards"] for e in events if e["type"] == "cards")
    chianti = next(c for c in cards if c["slug"] == "villa-antica-chianti-classico-2021")
    assert chianti["country"]
    assert chianti["grape_varieties"]
    assert chianti["stock_status"]


def test_relaxed_result_cards_flagged_closest_alternative(service: ChatService):
    # Only fortified wine in the seed catalogue is €22.95 — asking for one
    # under €1 forces the retriever to relax filters to find anything at all.
    events = _collect(service, "port under €1")
    cards_event = next(e for e in events if e["type"] == "cards")
    assert cards_event["cards"]
    assert all(c["closest_alternative"] for c in cards_event["cards"])
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "nothing matched your exact request" in text.lower()


def test_vague_first_message_asks_clarifying_question_not_cards(service: ChatService):
    events = _collect(service, "I need a wine for my anniversary dinner", session="vague1")
    types = [e["type"] for e in events]
    assert "cards" not in types
    assert "token" in types
    assert types[-1] == "done"


def test_specific_wine_name_first_message_skips_clarifying(service: ChatService):
    # "Chianti Classico" has no colour/price/country filter either, but is a
    # specific search — must not be mistaken for a vague request.
    events = _collect(service, "Chianti Classico", session="specific1")
    assert any(e["type"] == "cards" for e in events)


def test_second_message_proceeds_to_recommend_even_if_still_filterless(service: ChatService):
    session = "vague2"
    _collect(service, "I need a wine for my anniversary dinner", session=session)
    events = _collect(service, "something rich and memorable", session=session)
    types = [e["type"] for e in events]
    assert "cards" in types
    assert "token" in types


def test_vague_gate_never_fires_twice_in_one_session(service: ChatService):
    session = "vague3"
    _collect(service, "I need a wine", session=session)
    # Still vague-sounding, but this is no longer the first turn.
    events = _collect(service, "something nice please", session=session)
    assert any(e["type"] == "cards" for e in events)


def test_llm_error_yields_clean_error_event_not_an_exception(config: Config, reader: SnapshotReader):
    service = ChatService(reader, build_embedder(config), _BrokenLLM(), config)
    events = list(service.stream("s1", "hello"))
    types = [e["type"] for e in events]
    assert "error" in types
    assert "token" not in types
    assert types[-1] == "done"


def test_failed_clarifying_question_still_prevents_gate_from_refiring(
    config: Config, reader: SnapshotReader
):
    # Regression: a failed clarifying-question call used to return without
    # recording the turn, so `session.turns` stayed empty and a subsequent
    # vague-sounding message re-triggered the clarifying branch forever
    # instead of ever reaching a real recommendation.
    service = ChatService(reader, build_embedder(config), _BrokenLLM(), config)
    session = "vague-fail"
    first = list(service.stream(session, "I need a wine"))
    assert first[0]["type"] == "error"

    second = list(service.stream(session, "something nice please"))
    types = [e["type"] for e in second]
    assert "cards" in types  # reached the normal recommend path this time
