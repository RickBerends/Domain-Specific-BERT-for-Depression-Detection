"""Fixture-shop generator: structure, quirks, determinism."""

from __future__ import annotations

import os

import httpx

from ingest.fixture_shop import (
    dutch_price,
    generate,
    load_fixture_products,
    price_bucket,
    serve_fixture,
)


def test_dutch_price_formatting():
    assert dutch_price(1395) == "€ 13,95"
    assert dutch_price(800) == "€ 8,00"
    assert dutch_price(1050) == "€ 10,50"


def test_price_buckets():
    assert price_bucket(450) == "tot5euro"
    assert price_bucket(750) == "van5tot8euro"
    assert price_bucket(1200) == "van8tot15euro"
    assert price_bucket(2500) == "vanaf15euro"
    assert price_bucket(None) is None


def test_generate_emits_expected_structure(tmp_path):
    m = generate(str(tmp_path), load_fixture_products(15))
    assert m.product_ids and m.facet_pages and m.content_pages
    assert m.poisoned_ids  # poisoned by default
    assert os.path.exists(tmp_path / "robots.txt")
    assert os.path.exists(tmp_path / "sitemap.xml")
    assert os.path.exists(tmp_path / "index.html")
    # robots disallows the cart/account areas
    robots = (tmp_path / "robots.txt").read_text()
    assert "Disallow: /cart" in robots


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(str(a), load_fixture_products(15))
    generate(str(b), load_fixture_products(15))
    # every product HTML file is byte-identical across runs
    for root, _dirs, files in os.walk(a / "product"):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), a)
            assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_no_poison_flag_omits_defects(tmp_path):
    m = generate(str(tmp_path), load_fixture_products(15), poison=False)
    assert m.poisoned_ids == []


def test_served_urls_resolve_extensionless(tmp_path):
    m = generate(str(tmp_path), load_fixture_products(15))
    server, port = serve_fixture(str(tmp_path))
    try:
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(base + "/").status_code == 200
        assert httpx.get(base + "/rood").status_code == 200
        assert httpx.get(base + "/bezorging").status_code == 200
        # the deliberately-dead link 404s
        assert httpx.get(base + m.dead_link_target).status_code == 404
    finally:
        server.shutdown()
