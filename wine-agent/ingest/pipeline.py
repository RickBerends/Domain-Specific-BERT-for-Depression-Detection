"""End-to-end ingest pipeline: crawl → parse → validate → publish.

Reusable orchestration (imported by tests and by ``ingest.crawl``). Publishing
goes through the existing ``build_snapshot`` seam and happens **only if the
validation gates pass**, so a bad crawl leaves the served snapshot untouched.
Snapshot versioning/rollback and hot reload are a separate follow-up
(roadmap 3.4-lifecycle / 3.5); here we publish into the configured snapshot dir.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from schemas import Content, Product

from chat.config import Config
from chat.embeddings import build_embedder
from ingest.build_snapshot import build_snapshot
from ingest.crawler import Crawler, CrawlConfig
from ingest.parsers import ParseResult, parse_all
from ingest.validate import Thresholds, ValidationReport, validate


@dataclass
class PipelineResult:
    report: ValidationReport
    published: bool
    parse: ParseResult
    snapshot_id: str | None = None


def _previous(snapshot_dir: str) -> tuple[int | None, set[str]]:
    """Product count + slugs of the currently-published snapshot, if any."""
    db_path = os.path.join(snapshot_dir, "catalog.db")
    if not os.path.exists(db_path):
        return None, set()
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        slugs = {r[0] for r in db.execute("SELECT slug FROM products")}
    finally:
        db.close()
    return len(slugs), slugs


def run_pipeline(
    base_url: str,
    work_dir: str,
    config: Config,
    *,
    snapshot_id: str = "crawl",
    crawl_config: CrawlConfig | None = None,
    thresholds: Thresholds | None = None,
    reports_dir: str | None = None,
) -> PipelineResult:
    crawler = Crawler(base_url, work_dir, crawl_config or CrawlConfig())
    try:
        stats = crawler.crawl()
        parse = parse_all(crawler.pages())
    finally:
        crawler.close()

    prev_count, prev_slugs = _previous(config.snapshot_dir)
    report = validate(
        parse.products, stats,
        previous_count=prev_count, previous_slugs=prev_slugs,
        parse_errors=len(parse.errors), thresholds=thresholds,
    )

    if reports_dir:
        _write_reports(reports_dir, snapshot_id, report)

    published = False
    if report.passed:
        build_snapshot(
            config.snapshot_dir, parse.products, parse.contents,
            build_embedder(config), snapshot_id=snapshot_id,
        )
        published = True

    return PipelineResult(
        report=report, published=published, parse=parse,
        snapshot_id=snapshot_id if published else None,
    )


def _write_reports(reports_dir: str, snapshot_id: str, report: ValidationReport) -> None:
    import json

    os.makedirs(reports_dir, exist_ok=True)
    base = os.path.join(reports_dir, f"crawl-{snapshot_id}")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(report.to_markdown())
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(
            {"passed": report.passed, "failures": report.failures,
             "warnings": report.warnings, "stats": report.stats,
             "diff": {"new": report.diff.new, "removed": report.diff.removed,
                      "kept": len(report.diff.kept)}},
            f, indent=2,
        )
