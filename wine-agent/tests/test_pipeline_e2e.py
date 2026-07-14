"""End-to-end: generate → serve → crawl → parse → validate → publish."""

from __future__ import annotations

from chat.config import Config
from chat.snapshot import SnapshotReader
from ingest.crawler import CrawlConfig
from ingest.fixture_shop import DATA_DIR, generate, load_fixture_products, serve_fixture
from ingest.pipeline import run_pipeline


def _run(tmp_path, poison: bool):
    fixture = tmp_path / "fx"
    work = tmp_path / "wk"
    snap = tmp_path / "snap"
    generate(str(fixture), load_fixture_products(25), poison=poison)
    server, port = serve_fixture(str(fixture))
    try:
        cfg = Config(snapshot_dir=str(snap), embed_backend="fake", llm_backend="fake")
        return run_pipeline(
            f"http://127.0.0.1:{port}", str(work), cfg,
            snapshot_id="e2e", crawl_config=CrawlConfig(delay_seconds=0),
        ), str(snap)
    finally:
        server.shutdown()


def test_poisoned_crawl_is_quarantined(tmp_path):
    result, snap = _run(tmp_path, poison=True)
    assert result.published is False
    assert not result.report.passed
    # nothing was written to the snapshot dir
    import os
    assert not os.path.exists(os.path.join(snap, "catalog.db"))


def test_clean_crawl_publishes_servable_snapshot(tmp_path):
    result, snap = _run(tmp_path, poison=False)
    assert result.published is True
    assert result.report.passed

    reader = SnapshotReader(snap)
    assert reader.ref().snapshot_id == "e2e"
    assert reader.ref().product_count >= 20

    # ≥95% of parsed products carry price + color (facet-first worked)
    products = result.parse.products
    priced = sum(1 for p in products if p.price_cents is not None)
    colored = sum(1 for p in products if p.color_type is not None)
    assert priced / len(products) >= 0.95
    assert colored / len(products) >= 0.95

    # the published catalogue is actually queryable by the chat read path
    assert reader.lexical_search("rioja", 3)
    frascati = reader.get_product("prima-luna-frascati-2025")
    assert frascati is not None and frascati.price_cents == 795
