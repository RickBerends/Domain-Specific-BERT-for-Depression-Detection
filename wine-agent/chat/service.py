"""Chat orchestration — the request flow of technical plan §5.1.

Ties the pieces together: input guard → query planning → retrieve (traced) →
assemble prompt (with session history) → stream generation → emit the
product-card side-channel. Everything is streamed as a sequence of typed events
the API layer serializes to SSE. The product cards are built from retrieved
*metadata*, so the model never has to format a price itself (§5.1 step 7).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterator

from schemas import ProductCard, SnapshotRef

from chat.config import Config
from chat.embeddings import Embedder
from chat.images import card_image_url
from chat.lang import Language, detect_language
from chat.llm import FakeLLM, LLMClient, LLMError
from chat.planner import Filters, is_vague_request, plan
from chat.prompt import build_clarifying_system_prompt, build_system_prompt, build_user_message
from chat.retriever import HybridRetriever, select_recommendations
from chat.sessions import SessionStore
from chat.snapshot import SnapshotReader
from chat.tracing import span

_GUARD_EMPTY = {
    "en": "Please type a question about our wines.",
    "nl": "Typ een vraag over onze wijnen.",
}
_GUARD_TOO_LONG = {
    "en": "That message is too long. Please shorten it to under {n} characters.",
    "nl": "Dat bericht is te lang. Beperk het tot minder dan {n} tekens.",
}
_LLM_UNAVAILABLE = {
    "en": "The wine assistant is currently unavailable. Please try again shortly.",
    "nl": "De wijnassistent is momenteel niet bereikbaar. Probeer het straks opnieuw.",
}
# FakeLLM can only echo context or refuse — it structurally cannot generate a
# genuine clarifying question, so this canned bilingual fallback plays the
# same role there that the LLM-generated one plays for OllamaLLM.
_CLARIFYING_QUESTION = {
    "en": "Happy to help! What's the occasion, and do you prefer something bold and rich, or light and fresh?",
    "nl": "Graag geholpen! Wat is de gelegenheid, en hou je meer van iets stevigs en vols, of juist licht en fris?",
}


class ChatService:
    def __init__(
        self,
        reader: SnapshotReader,
        embedder: Embedder,
        llm: LLMClient,
        config: Config,
    ) -> None:
        self.reader = reader
        self.retriever = HybridRetriever(reader, embedder, top_k=config.top_k)
        self.llm = llm
        self.config = config
        self.card_limit = config.card_limit
        self.sessions = SessionStore(
            max_turns=config.history_turns * 2,  # user+assistant per exchange
            ttl_seconds=config.session_ttl_seconds,
        )

    def snapshot_ref(self) -> SnapshotRef:
        return self.reader.ref()

    def stream(self, session_id: str, message: str) -> Iterator[dict]:
        """Yield typed events: {type: token|cards|done|error, ...}."""
        language = detect_language(message)
        guard = self._guard(message, language)
        if guard is not None:
            yield {"type": "error", "message": guard}
            yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}
            return

        session = self.sessions.get(session_id)
        turn_plan = plan(message)
        filters = self._effective_filters(turn_plan, session)

        # A vague opening message ("I need a wine for my anniversary dinner")
        # has no colour/price/country signal AND no specific wine/grape/region
        # name — ask one clarifying question instead of guessing. (A message
        # like "Chianti Classico" also has no structured filter but IS
        # specific, so is_vague_request() requires a generic phrasing cue too
        # — not just the absence of filters.) Only on the very first turn:
        # `session.turns` is non-empty afterward (both branches below always
        # record both turns), so this can only fire once per session.
        if (
            turn_plan.route == "catalog"
            and not filters.any()
            and not session.turns
            and is_vague_request(message)
        ):
            yield from self._ask_clarifying(session_id, message, language)
            return

        with span("retrieve", query=message, session_id=session_id) as sp:
            sp.set_attribute("security.injection_suspected", suspected_injection)
            if turn_plan.route == "policy":
                result = self.retriever.retrieve_policy(message)
            else:
                result = self.retriever.retrieve(
                    message, filters, prefer_cheap=turn_plan.wants_cheaper
                )
            sp.set_attribute("route", turn_plan.route)
            sp.set_attribute("retrieved.products", len(result.products))
            sp.set_attribute("retrieved.contents", len(result.contents))
            sp.set_attribute("retrieved.relaxed", result.relaxed)

        # Curate up to card_limit complementary picks (best match / best value
        # / something different) rather than dumping the raw ranked list — and
        # narrow the LLM's context to exactly those so the reply talks about
        # the same wines the cards show, never a 4th/5th/6th unseen one.
        recommendations = select_recommendations(result.products, limit=self.card_limit)
        result = replace(result, products=[rec.product for rec in recommendations])

        # Product cards first: the widget can render them as soon as they arrive,
        # independent of the streamed prose.
        cards = [
            ProductCard.from_product(
                rec.product,
                closest_alternative=result.relaxed,
                role=rec.role,
                reason=rec.reason,
                fallback_image_url=card_image_url(
                    self.config.placeholder_image_base_url, rec.product, rec.role
                ),
            ).model_dump()
            for rec in recommendations
        ]
        if cards:
            yield {"type": "cards", "cards": cards}

        history = [(t.role, t.text) for t in session.turns]
        user_message = build_user_message(
            message, result, history=history, language=language, recommendations=recommendations
        )

        answer_parts: list[str] = []
        with span("generate", session_id=session_id):
            try:
                for token in self.llm.stream(
                    build_system_prompt(language), user_message
                ):
                    answer_parts.append(token)
                    yield {"type": "token", "text": token}
            except LLMError:
                # Still record the user's turn (no assistant reply to record)
                # so a subsequent message doesn't lose this from [HISTORY].
                self.sessions.record(session_id, "user", message)
                yield {"type": "error", "message": _LLM_UNAVAILABLE[language]}
                yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}
                return

        self._remember(session_id, message, "".join(answer_parts), filters, result)
        yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}

    def _ask_clarifying(self, session_id: str, message: str, language: Language) -> Iterator[dict]:
        """Ask one narrowing question instead of recommending — no retrieval,
        no cards, since there's nothing meaningful to ground a recommendation
        in yet. Records both turns so the customer's answer proceeds through
        the normal recommend path in ``stream()`` next time (with this
        exchange visible in ``[HISTORY]``), and so this never fires twice.
        """
        answer_parts: list[str] = []
        if isinstance(self.llm, FakeLLM):
            answer_parts.append(_CLARIFYING_QUESTION[language])
            yield {"type": "token", "text": answer_parts[0]}
        else:
            with span("generate_clarifying", session_id=session_id):
                try:
                    for token in self.llm.stream(
                        build_clarifying_system_prompt(language),
                        f"[LANGUAGE]{language}[/LANGUAGE]\n[QUESTION]\n{message}\n[/QUESTION]",
                    ):
                        answer_parts.append(token)
                        yield {"type": "token", "text": token}
                except LLMError:
                    # Record the user's turn even on failure: `session.turns`
                    # being non-empty is what stops the vague-request gate in
                    # stream() from firing again — without this, a transient
                    # LLM failure here would make every subsequent
                    # vague-sounding message re-trigger this branch instead
                    # of ever reaching a real recommendation.
                    self.sessions.record(session_id, "user", message)
                    yield {"type": "error", "message": _LLM_UNAVAILABLE[language]}
                    yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}
                    return

        self.sessions.record(session_id, "user", message)
        self.sessions.record(session_id, "assistant", "".join(answer_parts))
        yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}

    def _effective_filters(self, turn_plan, session) -> Filters:
        """Merge this turn's filters with session state for follow-ups."""
        if turn_plan.route != "catalog":
            return Filters()
        filters = turn_plan.filters
        previous = session.last_filters
        # inherit stable preferences (color/country) unless overridden this turn
        if filters.color_type is None:
            filters.color_type = previous.color_type
        if filters.country is None:
            filters.country = previous.country
        # "a cheaper one?" → cap below the cheapest option we already showed
        if (
            turn_plan.wants_cheaper
            and filters.max_price_cents is None
            and session.last_min_price_cents is not None
        ):
            filters.max_price_cents = session.last_min_price_cents - 1
        return filters

    def _remember(self, session_id, message, answer, filters, result) -> None:
        session = self.sessions.get(session_id)
        session.last_filters = filters
        prices = [p.price_cents for p in result.products if p.price_cents is not None]
        if prices:
            session.last_min_price_cents = min(prices)
        self.sessions.record(session_id, "user", message)
        self.sessions.record(session_id, "assistant", answer)

    def _guard(self, message: str, language: Language) -> str | None:
        """Cheap input guard (§5.1 step 2). Returns an error string or None."""
        text = (message or "").strip()
        if not text:
            return _GUARD_EMPTY[language]
        if len(text) > self.config.max_message_chars:
            return _GUARD_TOO_LONG[language].format(n=self.config.max_message_chars)
        return None


def build_service(config: Config) -> ChatService:
    """Compose a service from config — the single wiring point for backends."""
    from chat.embeddings import build_embedder
    from chat.llm import build_llm

    reader = SnapshotReader(config.snapshot_dir)
    embedder = build_embedder(config)
    llm = build_llm(config)
    return ChatService(reader, embedder, llm, config)
