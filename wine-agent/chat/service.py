"""Chat orchestration — the request flow of technical plan §5.1.

Ties the pieces together: input guard → query planning → retrieve (traced) →
assemble prompt (with session history) → stream generation → emit the
product-card side-channel. Everything is streamed as a sequence of typed events
the API layer serializes to SSE. The product cards are built from retrieved
*metadata*, so the model never has to format a price itself (§5.1 step 7).
"""

from __future__ import annotations

from typing import Iterator

from schemas import ProductCard, SnapshotRef

from chat.config import Config
from chat.embeddings import Embedder
from chat.llm import LLMClient
from chat.planner import Filters, plan
from chat.prompt import SYSTEM_PROMPT, build_user_message
from chat.retriever import HybridRetriever
from chat.security import looks_like_injection
from chat.sessions import SessionStore
from chat.snapshot import SnapshotReader
from chat.tracing import span


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
        self.sessions = SessionStore(
            max_turns=config.history_turns * 2,  # user+assistant per exchange
            ttl_seconds=config.session_ttl_seconds,
        )

    def snapshot_ref(self) -> SnapshotRef:
        return self.reader.ref()

    def stream(self, session_id: str, message: str) -> Iterator[dict]:
        """Yield typed events: {type: token|cards|done|error, ...}."""
        guard = self._guard(message)
        if guard is not None:
            yield {"type": "error", "message": guard}
            yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}
            return

        session = self.sessions.get(session_id)
        turn_plan = plan(message)
        filters = self._effective_filters(turn_plan, session)

        # Flag likely injection attempts for review. We do NOT reject them:
        # neutralization + the read-only design already contain the risk, and a
        # blocklist would only frustrate legitimate customers with false hits.
        suspected_injection = looks_like_injection(message)

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

        # Product cards first: the widget can render them as soon as they arrive,
        # independent of the streamed prose.
        cards = [ProductCard.from_product(p).model_dump() for p in result.products]
        if cards:
            yield {"type": "cards", "cards": cards}

        history = [(t.role, t.text) for t in session.turns]
        user_message = build_user_message(message, result, history=history)

        answer_parts: list[str] = []
        with span("generate", session_id=session_id):
            for token in self.llm.stream(SYSTEM_PROMPT, user_message):
                answer_parts.append(token)
                yield {"type": "token", "text": token}

        self._remember(session_id, message, "".join(answer_parts), filters, result)
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

    def _guard(self, message: str) -> str | None:
        """Cheap input guard (§5.1 step 2). Returns an error string or None."""
        text = (message or "").strip()
        if not text:
            return "Please type a question about our wines."
        if len(text) > self.config.max_message_chars:
            return (
                "That message is too long. Please shorten it to under "
                f"{self.config.max_message_chars} characters."
            )
        return None


def build_service(config: Config) -> ChatService:
    """Compose a service from config — the single wiring point for backends."""
    from chat.embeddings import build_embedder
    from chat.llm import build_llm

    reader = SnapshotReader(config.snapshot_dir)
    embedder = build_embedder(config)
    llm = build_llm(config)
    return ChatService(reader, embedder, llm, config)
