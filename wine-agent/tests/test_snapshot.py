"""Snapshot build + read contract."""

from __future__ import annotations

from schemas import Product, StockStatus

from chat.snapshot import SnapshotReader


def test_ref_reports_published_counts(reader: SnapshotReader):
    ref = reader.ref()
    assert ref.published is True
    assert ref.product_count == 20
    assert ref.content_count == 6


def test_get_product_roundtrips_full_model(reader: SnapshotReader):
    p = reader.get_product("prima-luna-frascati-2025")
    assert isinstance(p, Product)
    assert p.name == "Prima Luna Frascati"
    assert p.price_cents == 795
    assert "Malvasia" in p.grape_varieties


def test_stock_status_preserved(reader: SnapshotReader):
    assert reader.get_product("oakridge-barossa-shiraz-2021").stock_status is StockStatus.out
    assert reader.get_product("villa-antica-chianti-classico-2021").stock_status is StockStatus.limited


def test_lexical_search_finds_by_name(reader: SnapshotReader):
    hits = dict(reader.lexical_search("chianti", top_k=5))
    assert "villa-antica-chianti-classico-2021" in hits


def test_content_search_finds_policy(reader: SnapshotReader):
    hits = reader.content_search("delivery shipping free", top_k=3)
    assert any("Delivery" in c.title for c in hits)


def test_fts_query_survives_punctuation(reader: SnapshotReader):
    # Should not raise an FTS5 syntax error on punctuation-heavy input.
    assert reader.lexical_search("red wine under €15?!", top_k=3) is not None
