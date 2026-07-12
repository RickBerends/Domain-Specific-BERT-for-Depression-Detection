"""Hybrid retrieval (technical plan §5.1 step 4, §4.5).

Vector similarity + FTS5 lexical search, fused with Reciprocal Rank Fusion.
RRF is used deliberately: wine/producer names are exact-match-heavy (lexical
wins) while descriptive queries need semantics (vector wins), and RRF merges
the two rankings without having to normalize their incomparable score scales.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schemas import Content, Product

from chat.embeddings import Embedder
from chat.snapshot import SnapshotReader

RRF_K = 60  # standard RRF damping constant


@dataclass
class RetrievalResult:
    products: list[Product] = field(default_factory=list)
    contents: list[Content] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.products and not self.contents


class HybridRetriever:
    def __init__(
        self, reader: SnapshotReader, embedder: Embedder, top_k: int = 6
    ) -> None:
        self.reader = reader
        self.embedder = embedder
        self.top_k = top_k

    def retrieve(self, query: str) -> RetrievalResult:
        # candidate pools slightly wider than top_k so fusion has room to work
        pool = self.top_k * 3

        query_vec = self.embedder.embed([query])[0]
        vector_slugs = [h.id for h in self.reader.vector_search(query_vec, pool)]
        lexical_slugs = [slug for slug, _ in self.reader.lexical_search(query, pool)]

        fused = _rrf_fuse(vector_slugs, lexical_slugs)[: self.top_k]

        products: list[Product] = []
        for slug in fused:
            product = self.reader.get_product(slug)
            if product is not None:
                products.append(product)

        contents = self.reader.content_search(query, top_k=2)
        return RetrievalResult(products=products, contents=contents)


def _rrf_fuse(*ranked_lists: list[str]) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda item: scores[item], reverse=True)
