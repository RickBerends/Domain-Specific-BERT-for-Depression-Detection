"""Crawler: classification, politeness, resume, raw store."""

from __future__ import annotations

from ingest.crawler import Crawler, CrawlConfig, classify
from ingest.fixture_shop import generate, load_fixture_products, serve_fixture


def test_url_classification():
    assert classify("/product/123/prima-luna") == "product"
    assert classify("/rood") == "category"
    assert classify("/modelwijnen/vol-verfijnd") == "category"
    assert classify("/bezorging") == "content"
    assert classify("/cart") == "ignore"
    assert classify("/account/orders") == "ignore"
    assert classify("/") == "category"


def test_crawl_fetches_and_classifies(crawled_pages):
    _manifest, pages, stats = crawled_pages
    assert stats.fetched > 20
    assert stats.by_class.get("product", 0) > 0
    assert stats.by_class.get("category", 0) > 0
    assert stats.by_class.get("content", 0) > 0
    assert len(pages) == stats.fetched


def test_dead_link_recorded(crawled_pages):
    _manifest, _pages, stats = crawled_pages
    assert stats.dead_links >= 1


def test_disallowed_paths_not_fetched(tmp_path):
    fixture, work = tmp_path / "fx", tmp_path / "wk"
    generate(str(fixture), load_fixture_products(10))
    server, port = serve_fixture(str(fixture))
    try:
        c = Crawler(f"http://127.0.0.1:{port}", str(work), CrawlConfig(delay_seconds=0))
        # a /cart link would be classified 'ignore' and never enqueued
        c._enqueue(f"http://127.0.0.1:{port}/cart", 0)
        rows = c._db.execute("SELECT COUNT(*) FROM frontier WHERE url LIKE '%/cart'").fetchone()
        assert rows[0] == 0
        c.close()
    finally:
        server.shutdown()


def test_resume_after_interrupt(tmp_path):
    fixture, work = tmp_path / "fx", tmp_path / "wk"
    generate(str(fixture), load_fixture_products(15))
    server, port = serve_fixture(str(fixture))
    try:
        base = f"http://127.0.0.1:{port}"
        # first crawler: cap at a few pages to simulate interruption
        c1 = Crawler(base, str(work), CrawlConfig(delay_seconds=0, max_pages=5))
        c1.crawl()
        done_first = c1._db.execute(
            "SELECT COUNT(*) FROM frontier WHERE state='done'").fetchone()[0]
        pending_first = c1._db.execute(
            "SELECT COUNT(*) FROM frontier WHERE state='pending'").fetchone()[0]
        c1.close()
        assert pending_first > 0  # work left over

        # second crawler reuses the same frontier DB and finishes the rest
        c2 = Crawler(base, str(work), CrawlConfig(delay_seconds=0))
        c2.crawl()
        done_second = c2._db.execute(
            "SELECT COUNT(*) FROM frontier WHERE state='done'").fetchone()[0]
        c2.close()
        assert done_second > done_first
    finally:
        server.shutdown()
