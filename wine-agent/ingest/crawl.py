"""Crawl driver CLI (roadmap 3.4).

    python -m ingest.crawl --serve-fixture         # generate+serve a mock shop, crawl it
    python -m ingest.crawl --base-url https://...  # crawl an already-running site

Generates/serves the fixture shop when asked, runs the full pipeline (crawl →
parse → validate → publish-if-gates-pass), writes a diff report, and exits
nonzero if the gates fail (so it can gate CI / a cron job).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from chat.config import load_config
from ingest.crawler import CrawlConfig
from ingest.fixture_shop import DATA_DIR, generate, load_fixture_products, serve_fixture
from ingest.pipeline import run_pipeline


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve-fixture", action="store_true",
                       help="generate + serve a mock shop and crawl it")
    group.add_argument("--base-url", help="crawl an already-running site")
    ap.add_argument("--n", type=int, default=60, help="fixture size")
    ap.add_argument("--no-poison", action="store_true", help="clean fixture (no defects)")
    ap.add_argument("--delay", type=float, default=1.0, help="polite delay per request (s)")
    ap.add_argument("--snapshot-id", default="crawl")
    args = ap.parse_args()

    config = load_config()
    work_dir = tempfile.mkdtemp(prefix="wine-crawl-")
    reports_dir = os.path.join(DATA_DIR, "reports")

    server = None
    try:
        if args.serve_fixture:
            fixture_dir = os.path.join(DATA_DIR, "fixture_shop")
            generate(fixture_dir, load_fixture_products(args.n), poison=not args.no_poison)
            server, port = serve_fixture(fixture_dir)
            base_url = f"http://127.0.0.1:{port}"
        else:
            base_url = args.base_url

        result = run_pipeline(
            base_url, work_dir, config,
            snapshot_id=args.snapshot_id,
            crawl_config=CrawlConfig(delay_seconds=args.delay),
            reports_dir=reports_dir,
        )
    finally:
        if server is not None:
            server.shutdown()

    r = result.report
    print(r.to_markdown())
    if result.published:
        print(f"Published snapshot {result.snapshot_id!r} to {config.snapshot_dir}")
        return 0
    print("NOT PUBLISHED — validation gates failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
