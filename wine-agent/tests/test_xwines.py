"""X-Wines mapper on the vendored fixture (offline)."""

from __future__ import annotations

import os

from schemas import ColorType, Language

from ingest.xwines import load_products, row_to_product

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "xwines_sample.csv")


def test_fixture_loads_all_rows():
    products = load_products(FIXTURE)
    assert len(products) == 10


def test_mapping_fields():
    products = {p.slug: p for p in load_products(FIXTURE)}
    merlot = next(p for p in products.values() if "origem-merlot" in p.slug)
    assert merlot.color_type is ColorType.red
    assert merlot.grape_varieties == ["Merlot"]
    assert "Beef" in merlot.food_pairing
    assert merlot.producer == "Casa Valduga"
    assert merlot.country == "Brazil"
    assert merlot.abv == 13.0
    assert merlot.language is Language.en


def test_synthetic_fields_are_deterministic():
    a = load_products(FIXTURE)
    b = load_products(FIXTURE)
    assert [p.price_cents for p in a] == [p.price_cents for p in b]
    assert [p.stock_status for p in a] == [p.stock_status for p in b]


def test_prices_in_plausible_band():
    for p in load_products(FIXTURE):
        assert 500 <= p.price_cents <= 5000


def test_tasting_note_synthesized_from_facts():
    products = load_products(FIXTURE)
    for p in products:
        assert p.tasting_notes
        assert p.tasting_notes.startswith("A ")


def test_dessert_port_maps_to_fortified():
    row = {
        "WineID": "1", "WineName": "Testy Port", "Type": "Dessert/Port",
        "Elaborate": "", "Grapes": "['Touriga Nacional']", "Harmonize": "['Cheese']",
        "ABV": "20.0", "Body": "Full-bodied", "Acidity": "Low", "Code": "PT",
        "Country": "Portugal", "RegionID": "1", "RegionName": "Douro",
        "WineryID": "1", "WineryName": "Testy", "Website": "", "Vintages": "[2010]",
    }
    assert row_to_product(row).color_type is ColorType.fortified
