"""Shared fixtures: build a throwaway snapshot once per test session."""

from __future__ import annotations

import json
import os

import pytest

from schemas import Content, Product

from chat.config import Config
from chat.embeddings import build_embedder
from chat.service import ChatService, build_service
from chat.snapshot import SnapshotReader
from ingest.build_snapshot import build_snapshot

ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT, "data")


def _load(name: str) -> list[dict]:
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def snapshot_dir(tmp_path_factory) -> str:
    out = str(tmp_path_factory.mktemp("snapshot"))
    cfg = Config(snapshot_dir=out)
    products = [Product.model_validate(d) for d in _load("seed_products.json")]
    contents = [Content.model_validate(d) for d in _load("seed_content.json")]
    build_snapshot(out, products, contents, build_embedder(cfg), snapshot_id="test")
    return out


@pytest.fixture()
def config(snapshot_dir: str) -> Config:
    return Config(snapshot_dir=snapshot_dir, llm_backend="fake", embed_backend="fake")


@pytest.fixture()
def reader(snapshot_dir: str) -> SnapshotReader:
    return SnapshotReader(snapshot_dir)


@pytest.fixture()
def service(config: Config) -> ChatService:
    return build_service(config)


@pytest.fixture(scope="session")
def crawled_pages(tmp_path_factory):
    """Generate a small fixture shop, crawl it once, return (manifest, pages, stats)."""
    from ingest.crawler import Crawler, CrawlConfig
    from ingest.fixture_shop import generate, load_fixture_products, serve_fixture

    fixture_dir = str(tmp_path_factory.mktemp("fixture"))
    work_dir = str(tmp_path_factory.mktemp("crawl"))
    manifest = generate(fixture_dir, load_fixture_products(20))
    server, port = serve_fixture(fixture_dir)
    try:
        crawler = Crawler(f"http://127.0.0.1:{port}", work_dir, CrawlConfig(delay_seconds=0))
        stats = crawler.crawl()
        pages = crawler.pages()
        crawler.close()
    finally:
        server.shutdown()
    return manifest, pages, stats
