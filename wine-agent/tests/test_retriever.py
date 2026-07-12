"""Hybrid retrieval behaviour."""

from __future__ import annotations

from chat.config import Config
from chat.embeddings import build_embedder
from chat.retriever import HybridRetriever, _rrf_fuse
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
