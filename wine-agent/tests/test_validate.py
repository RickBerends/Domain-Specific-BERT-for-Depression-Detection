"""Validation gates + diff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from schemas import Product

from ingest.validate import Thresholds, diff_products, validate


@dataclass
class _Stats:
    fetched: int = 100
    dead_links: int = 0


def _p(slug, price=1000, **kw):
    return Product(slug=slug, name=slug, price_cents=price, **kw)


def test_clean_set_passes():
    report = validate([_p("a"), _p("b")], _Stats())
    assert report.passed
    assert report.stats["products"] == 2


def test_expired_offer_fails():
    past = datetime.now(timezone.utc) - timedelta(days=5)
    report = validate([_p("a"), _p("b", offer_valid_until=past)], _Stats())
    assert not report.passed
    assert any("expired offer" in f for f in report.failures)


def test_catalogue_shrink_fails():
    report = validate([_p("a")], _Stats(), previous_count=100)
    assert not report.passed
    assert any("shrank" in f for f in report.failures)


def test_missing_prices_fail_threshold():
    products = [_p(f"p{i}") for i in range(9)] + [Product(slug="x", name="x")]
    # 9/10 = 90% present → passes at default; drop to 8/10 → fails
    products = [_p(f"p{i}") for i in range(8)] + [Product(slug="x", name="x"),
                                                  Product(slug="y", name="y")]
    report = validate(products, _Stats())
    assert not report.passed
    assert any("have a price" in f for f in report.failures)


def test_price_over_cap_fails():
    report = validate([_p("a", price=5_000_00)], _Stats(),
                      thresholds=Thresholds(price_cap_cents=100_000))
    assert not report.passed
    assert any("sanity cap" in f for f in report.failures)


def test_dead_link_rate_fails():
    report = validate([_p("a")], _Stats(fetched=10, dead_links=5))
    assert not report.passed
    assert any("dead-link" in f for f in report.failures)


def test_diff_products():
    prev = {"a", "b", "c"}
    d = diff_products(prev, [_p("b"), _p("c"), _p("d")])
    assert d.new == ["d"]
    assert d.removed == ["a"]
    assert sorted(d.kept) == ["b", "c"]
