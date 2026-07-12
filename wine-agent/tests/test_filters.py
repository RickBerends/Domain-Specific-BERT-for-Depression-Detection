"""Filtered retrieval: constraints hold, relaxation works, policy route."""

from __future__ import annotations

from schemas import ColorType

from chat.config import Config
from chat.embeddings import build_embedder
from chat.planner import Filters
from chat.retriever import HybridRetriever
from chat.snapshot import SnapshotReader


def _retriever(reader: SnapshotReader, config: Config) -> HybridRetriever:
    return HybridRetriever(reader, build_embedder(config), top_k=config.top_k)


def test_red_under_15_only_returns_matching(reader, config):
    filters = Filters(color_type=ColorType.red, max_price_cents=1500)
    result = _retriever(reader, config).retrieve("red wine", filters)
    assert result.products
    assert not result.relaxed
    for p in result.products:
        assert p.color_type is ColorType.red
        assert p.price_cents <= 1500


def test_country_filter(reader, config):
    filters = Filters(country="France")
    result = _retriever(reader, config).retrieve("wine", filters)
    assert result.products
    assert all(p.country == "France" for p in result.products)


def test_impossible_filter_relaxes(reader, config):
    # nothing costs less than €1 — filters relax and the result says so
    filters = Filters(max_price_cents=100)
    result = _retriever(reader, config).retrieve("wine", filters)
    assert result.relaxed
    assert result.products, "relaxation should surface alternatives"


def test_filters_without_text_terms(reader, config):
    # message tokens that carry no lexical signal still yield filtered products
    filters = Filters(color_type=ColorType.sparkling)
    result = _retriever(reader, config).retrieve("iets voor een feestje", filters)
    assert result.products
    assert all(p.color_type is ColorType.sparkling for p in result.products) or result.relaxed


def test_policy_route_returns_content_only(reader, config):
    result = _retriever(reader, config).retrieve_policy("opening hours")
    assert result.contents
    assert result.products == []
