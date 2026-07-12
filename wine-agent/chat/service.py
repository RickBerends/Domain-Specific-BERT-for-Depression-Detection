"""Chat orchestration — the request flow of technical plan §5.1.

Ties the pieces together: input guard → retrieve (traced) → assemble prompt →
stream generation → emit the product-card side-channel. Everything is streamed
as a sequence of typed events the API layer serializes to SSE. The product
cards are built from retrieved *metadata*, so the model never has to format a
price itself (§5.1 step 7).
"""

from __future__ import annotations

from typing import Iterator

from schemas import ProductCard, SnapshotRef

from chat.config import Config
from chat.embeddings import Embedder
from chat.llm import LLMClient
from chat.prompt import SYSTEM_PROMPT, build_user_message
from chat.retriever import HybridRetriever
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

    def snapshot_ref(self) -> SnapshotRef:
        return self.reader.ref()

    def stream(self, session_id: str, message: str) -> Iterator[dict]:
        """Yield typed events: {type: token|cards|done|error, ...}."""
        guard = self._guard(message)
        if guard is not None:
            yield {"type": "error", "message": guard}
            yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}
            return

        with span("retrieve", query=message, session_id=session_id) as sp:
            result = self.retriever.retrieve(message)
            sp.set_attribute("retrieved.products", len(result.products))
            sp.set_attribute("retrieved.contents", len(result.contents))

        # Product cards first: the widget can render them as soon as they arrive,
        # independent of the streamed prose.
        cards = [ProductCard.from_product(p).model_dump() for p in result.products]
        if cards:
            yield {"type": "cards", "cards": cards}

        user_message = build_user_message(message, result)
        with span("generate", session_id=session_id):
            for token in self.llm.stream(SYSTEM_PROMPT, user_message):
                yield {"type": "token", "text": token}

        yield {"type": "done", "snapshot_id": self.reader.ref().snapshot_id}

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
