"""Hybrid retrieval (technical plan §5.1 step 4, §4.5).

Vector similarity + FTS5 lexical search, fused with Reciprocal Rank Fusion.
RRF is used deliberately: wine/producer names are exact-match-heavy (lexical
wins) while descriptive queries need semantics (vector wins), and RRF merges
the two rankings without having to normalize their incomparable score scales.

Planner filters constrain both branches *before* fusion — the lexical branch in
SQL, the vector branch on the metadata stored with each embedding. If a
filtered search comes back empty the filters are relaxed once and the result is
marked, so the answer can say "nothing matches exactly; closest options:".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schemas import Content, Product

from chat.embeddings import Embedder
from chat.planner import Filters
from chat.snapshot import SnapshotReader
from chat.vectorstore import VectorHit

RRF_K = 60  # standard RRF damping constant


@dataclass
class RetrievalResult:
    products: list[Product] = field(default_factory=list)
    contents: list[Content] = field(default_factory=list)
    relaxed: bool = False  # filters matched nothing; these are unfiltered alternatives

    def is_empty(self) -> bool:
        return not self.products and not self.contents


@dataclass
class Recommendation:
    product: Product
    role: str  # "best_match" | "best_value" | "different"
    reason: str


def select_recommendations(products: list[Product], limit: int = 3) -> list[Recommendation]:
    """Curate up to ``limit`` labeled picks from an already-ranked product list.

    Rather than just showing raw top-N results, pick three complementary
    roles: the top-ranked match, the cheapest option that still qualified,
    and the most diverse remaining pick (different country or grape variety
    from the top match) — so each card has a clear, honest reason to exist.
    Reasons are deterministic templates built from real product fields, never
    LLM-generated, so they're free and identical across both LLM backends.
    """
    if not products:
        return []

    picks: list[Recommendation] = []
    used: set[str] = set()

    best_match = products[0]
    picks.append(Recommendation(best_match, "best_match", "Closest match to your request"))
    used.add(best_match.slug)

    priced = [p for p in products if p.slug not in used and p.price_cents is not None]
    if priced:
        best_value = min(priced, key=lambda p: p.price_cents)
        picks.append(
            Recommendation(best_value, "best_value", "The best-value option that still fits your request")
        )
        used.add(best_value.slug)

    def _is_different(p: Product) -> bool:
        if p.country and best_match.country and p.country != best_match.country:
            return True
        grapes = set(p.grape_varieties)
        match_grapes = set(best_match.grape_varieties)
        return bool(grapes) and not (grapes & match_grapes)

    remaining = [p for p in products if p.slug not in used]
    different = next((p for p in remaining if _is_different(p)), None) or (
        remaining[0] if remaining else None
    )
    if different is not None:
        bits = [
            b
            for b in (
                different.country if different.country != best_match.country else None,
                different.grape_varieties[0] if different.grape_varieties else None,
            )
            if b
        ]
        detail = " ".join(bits) if bits else "a different style"
        picks.append(Recommendation(different, "different", f"Something different — {detail}"))
        used.add(different.slug)

    return picks[:limit]


def _matches(hit: VectorHit, filters: Filters) -> bool:
    meta = hit.metadata
    if filters.color_type is not None and meta.get("color_type") != filters.color_type.value:
        return False
    if filters.country is not None and meta.get("country") != filters.country:
        return False
    price = meta.get("price_cents")
    if filters.max_price_cents is not None and (price is None or price > filters.max_price_cents):
        return False
    if filters.min_price_cents is not None and (price is None or price < filters.min_price_cents):
        return False
    return True


class HybridRetriever:
    def __init__(
        self, reader: SnapshotReader, embedder: Embedder, top_k: int = 6
    ) -> None:
        self.reader = reader
        self.embedder = embedder
        self.top_k = top_k

    def retrieve(
        self,
        query: str,
        filters: Filters | None = None,
        prefer_cheap: bool = False,
    ) -> RetrievalResult:
        filters = filters or Filters()
        slugs = self._product_slugs(query, filters, prefer_cheap)
        relaxed = False
        if not slugs and filters.any():
            # nothing satisfies the filters — relax in stages so alternatives
            # stay close: drop the price bounds first, then everything
            relaxed = True
            softer = Filters(color_type=filters.color_type, country=filters.country)
            if softer.any():
                slugs = self._product_slugs(query, softer, prefer_cheap)
            if not slugs:
                slugs = self._product_slugs(query, Filters(), prefer_cheap)

        products: list[Product] = []
        for slug in slugs[: self.top_k]:
            product = self.reader.get_product(slug)
            if product is not None:
                products.append(product)

        contents = self.reader.content_search(query, top_k=2)
        return RetrievalResult(products=products, contents=contents, relaxed=relaxed)

    def retrieve_policy(self, query: str) -> RetrievalResult:
        """Policy route (§5.1 step 3): content pages only, wider net."""
        return RetrievalResult(contents=self.reader.content_search(query, top_k=4))

    def _product_slugs(
        self, query: str, filters: Filters, prefer_cheap: bool = False
    ) -> list[str]:
        # candidate pools slightly wider than top_k so fusion has room to work
        pool = self.top_k * 3

        if prefer_cheap and filters.any():
            # "a cheaper one?" — price order is the relevance order
            return self.reader.filter_products(filters, pool)

        query_vec = self.embedder.embed([query])[0]
        vector_hits = self.reader.vector_search(query_vec, pool * 3)
        vector_slugs = [h.id for h in vector_hits if _matches(h, filters)][:pool]
        lexical_slugs = [
            slug for slug, _ in self.reader.lexical_search(query, pool, filters)
        ]

        fused = _rrf_fuse(vector_slugs, lexical_slugs)
        if not fused and filters.any():
            # filters without useful text terms ("something under €10")
            fused = self.reader.filter_products(filters, pool)
        return fused


def _rrf_fuse(*ranked_lists: list[str]) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda item: scores[item], reverse=True)
