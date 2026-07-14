"""Hybrid retrieval behaviour."""

from __future__ import annotations

from schemas import ColorType, Product

from chat.config import Config
from chat.embeddings import build_embedder
from chat.retriever import HybridRetriever, _rrf_fuse, select_recommendations
from chat.snapshot import SnapshotReader


def _retriever(reader: SnapshotReader, config: Config) -> HybridRetriever:
    return HybridRetriever(reader, build_embedder(config), top_k=config.top_k)


def test_exact_name_query_returns_that_wine(reader, config):
    result = _retriever(reader, config).retrieve("Chianti Classico")
    slugs = [p.slug for p in result.products]
    assert "villa-antica-chianti-classico-2021" in slugs


def test_pairing_query_returns_products_and_content(reader, config):
    result = _retriever(reader, config).retrieve("wine to pair with oysters")
    assert result.products, "expected at least one product"
    assert len(result.contents) <= 2, "catalog route caps content at 2 chunks"


def test_respects_top_k(reader, config):
    result = _retriever(reader, config).retrieve("wine")
    assert len(result.products) <= config.top_k


def test_rrf_rewards_agreement():
    # An item ranked highly by both lists should win over one ranked high by one.
    fused = _rrf_fuse(["a", "b", "c"], ["b", "a", "d"])
    assert fused[0] in {"a", "b"}
    assert set(fused) == {"a", "b", "c", "d"}


def _product(slug: str, price_cents: int, country: str = "France", grapes=None) -> Product:
    return Product(
        slug=slug,
        name=slug,
        price_cents=price_cents,
        country=country,
        grape_varieties=grapes or [],
        color_type=ColorType.red,
    )


def test_select_recommendations_assigns_three_distinct_roles():
    products = [
        _product("a", 2000, country="France", grapes=["Merlot"]),
        _product("b", 1000, country="France", grapes=["Merlot"]),
        _product("c", 1500, country="Italy", grapes=["Sangiovese"]),
    ]
    recs = select_recommendations(products, limit=3)
    assert {r.role for r in recs} == {"best_match", "best_value", "different"}
    assert recs[0].product.slug == "a"  # top-ranked stays best_match
    assert recs[1].product.slug == "b"  # cheapest of the rest
    assert recs[2].product.slug == "c"  # different country + grape


def test_select_recommendations_respects_limit():
    products = [_product(str(i), 1000 + i) for i in range(5)]
    assert len(select_recommendations(products, limit=1)) == 1


def test_select_recommendations_handles_single_product():
    recs = select_recommendations([_product("only", 999)], limit=3)
    assert len(recs) == 1
    assert recs[0].role == "best_match"


def test_select_recommendations_empty_input():
    assert select_recommendations([], limit=3) == []
