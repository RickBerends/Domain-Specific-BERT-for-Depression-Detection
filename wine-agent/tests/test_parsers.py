"""Parsers: Dutch normalizers, extraction, resilience."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas import ColorType, StockStatus

from ingest.parsers import (
    parse_all,
    parse_dutch_price,
    parse_offer_date,
    parse_stock,
    parse_tiers,
    vintage_from_slug,
)


def test_parse_dutch_price():
    assert parse_dutch_price("€ 13,95 per fles") == 1395
    assert parse_dutch_price("€ 8,00 per fles") == 800
    assert parse_dutch_price("geen prijs") is None


def test_parse_stock():
    assert parse_stock("Op voorraad") == (StockStatus.in_stock, None)
    assert parse_stock("Nog 6 flessen beschikbaar") == (StockStatus.limited, 6)
    assert parse_stock("Beperkt beschikbaar") == (StockStatus.limited, None)
    assert parse_stock("Niet op voorraad") == (StockStatus.out, None)


def test_parse_tiers():
    tiers = parse_tiers("Vanaf 6 flessen € 10,45 per fles")
    assert len(tiers) == 1
    assert tiers[0].min_qty == 6 and tiers[0].price_cents == 1045


def test_parse_offer_date():
    d = parse_offer_date("Aanbieding geldig t/m 31 July 2026")
    assert d == datetime(2026, 7, 31, tzinfo=timezone.utc)
    assert parse_offer_date("geen aanbieding") is None


def test_vintage_from_slug():
    assert vintage_from_slug("prima-luna-frascati-2025") == 2025
    assert vintage_from_slug("cava-estrella-brut-nv") is None


def test_parse_all_roundtrips_products(crawled_pages):
    _manifest, pages, _stats = crawled_pages
    result = parse_all(pages)
    assert len(result.products) >= 15
    assert result.contents  # policy/guide pages parsed

    by_slug = {p.slug: p for p in result.products}
    frascati = by_slug["prima-luna-frascati-2025"]
    assert frascati.color_type is ColorType.white  # from facet membership
    assert frascati.country == "Italy"
    assert frascati.price_cents == 795
    assert "Malvasia" in frascati.grape_varieties
    assert frascati.food_pairing  # "Lekker bij: …" parsed


def test_duplicate_ids_deduped(crawled_pages):
    _manifest, pages, _stats = crawled_pages
    result = parse_all(pages)
    slugs = [p.slug for p in result.products]
    # the duplicate poisoned page (same canonical id) is kept once
    assert slugs.count("dubbele-wijn") <= 1


def test_malformed_page_does_not_crash(crawled_pages):
    _manifest, pages, _stats = crawled_pages
    # parse_all must complete; any unparseable page is recorded, not raised
    result = parse_all(pages)
    assert isinstance(result.errors, list)
