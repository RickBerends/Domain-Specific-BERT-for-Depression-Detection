"""Polite, resumable crawler (roadmap 3.2, technical plan §4.2).

Fetches a single site into a raw-HTML store, with a SQLite frontier so an
interrupted run resumes instead of restarting. Politeness is non-negotiable
(§4.1): robots.txt is honored, requests carry an identifying User-Agent with a
contact, they are rate-limited and same-domain only, and conditional GETs use
stored ETag/Last-Modified. Raw HTML is stored compressed keyed by URL so
parsers can be improved and re-run **without re-crawling**.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import sqlite3
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

USER_AGENT = "WineshopBot/1.0 (+https://wineshop.example/bot; contact bot@wineshop.example)"

# URL classification (roadmap 3.2). Only classified pages are stored/parsed.
_CLASS_RULES: list[tuple[str, re.Pattern]] = [
    ("ignore", re.compile(r"^/(cart|account|checkout|login|zoeken)\b")),
    ("product", re.compile(r"^/product/\d+/[^/]+$")),
    ("content", re.compile(
        r"^/(bezorging|herroepingsrecht|klantenservice|wijnspijswijzer|"
        r"begrippenlijst|geborgde-werkwijze|over-ons|veelgestelde)")),
    ("category", re.compile(r"^/[a-z0-9-]+(?:/[a-z0-9-]+)?$")),  # facets, incl. modelwijnen/*
]


def classify(path: str) -> str:
    path = path.rstrip("/") or "/"
    for label, rule in _CLASS_RULES:
        if rule.match(path):
            return label
    if path == "/":
        return "category"
    return "ignore"


@dataclass
class CrawlStats:
    fetched: int = 0
    from_cache: int = 0
    errors: int = 0
    dead_links: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    off_domain_skipped: int = 0
    disallowed_skipped: int = 0


@dataclass
class CrawlConfig:
    delay_seconds: float = 1.0          # ≥1s in prod; ~0 in tests
    max_pages: int = 5000
    timeout: float = 30.0
    max_retries: int = 3


class Crawler:
    def __init__(self, base_url: str, out_dir: str, config: CrawlConfig | None = None):
        self.base = base_url.rstrip("/")
        self.host = urlparse(self.base).netloc
        self.out_dir = out_dir
        self.raw_dir = os.path.join(out_dir, "raw")
        os.makedirs(self.raw_dir, exist_ok=True)
        self.config = config or CrawlConfig()
        self.stats = CrawlStats()
        self._db = sqlite3.connect(os.path.join(out_dir, "crawl.db"))
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS frontier (
                   url TEXT PRIMARY KEY, state TEXT, klass TEXT, depth INTEGER,
                   etag TEXT, last_modified TEXT, content_hash TEXT, raw_path TEXT
               )"""
        )
        self._db.commit()
        self._robots = self._load_robots()

    # --- robots ---
    def _load_robots(self) -> urllib.robotparser.RobotFileParser:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = httpx.get(f"{self.base}/robots.txt",
                             headers={"User-Agent": USER_AGENT}, timeout=self.config.timeout)
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])
        return rp

    def _allowed(self, url: str) -> bool:
        return self._robots.can_fetch(USER_AGENT, url)

    # --- frontier ---
    def _enqueue(self, url: str, depth: int) -> None:
        path = urlparse(url).path.rstrip("/") or "/"
        klass = classify(path)
        if klass == "ignore":
            return
        self._db.execute(
            "INSERT OR IGNORE INTO frontier (url, state, klass, depth) VALUES (?,?,?,?)",
            (url, "pending", klass, depth),
        )

    def _next(self) -> tuple[str, str, int, str | None, str | None] | None:
        row = self._db.execute(
            "SELECT url, klass, depth, etag, last_modified FROM frontier "
            "WHERE state='pending' ORDER BY depth, url LIMIT 1"
        ).fetchone()
        return row

    def _same_host(self, url: str) -> bool:
        netloc = urlparse(url).netloc
        return netloc == "" or netloc == self.host

    # --- crawl loop ---
    def crawl(self) -> CrawlStats:
        # seed from sitemap, else homepage
        if not self._seed_from_sitemap():
            self._enqueue(self.base + "/", 0)
        self._db.commit()

        processed = 0
        with httpx.Client(headers={"User-Agent": USER_AGENT},
                          timeout=self.config.timeout, follow_redirects=True) as client:
            while processed < self.config.max_pages:
                row = self._next()
                if row is None:
                    break
                url, klass, depth, etag, last_mod = row
                processed += 1
                self._fetch_one(client, url, klass, depth, etag, last_mod)
                self._db.commit()
                if self.config.delay_seconds:
                    time.sleep(self.config.delay_seconds)
        return self.stats

    def _seed_from_sitemap(self) -> bool:
        try:
            resp = httpx.get(f"{self.base}/sitemap.xml",
                             headers={"User-Agent": USER_AGENT}, timeout=self.config.timeout)
            if resp.status_code != 200:
                return False
        except httpx.HTTPError:
            return False
        locs = re.findall(r"<loc>([^<]+)</loc>", resp.text)
        for loc in locs:
            self._enqueue(urljoin(self.base + "/", loc), 1)
        return bool(locs)

    def _fetch_one(self, client, url, klass, depth, etag, last_mod) -> None:
        if not self._same_host(url):
            self.stats.off_domain_skipped += 1
            self._set_state(url, "skipped")
            return
        if not self._allowed(url):
            self.stats.disallowed_skipped += 1
            self._set_state(url, "disallowed")
            return

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_mod:
            headers["If-Modified-Since"] = last_mod

        resp = self._get_with_retry(client, url, headers)
        if resp is None:
            self.stats.errors += 1
            self._set_state(url, "error")
            return
        if resp.status_code == 304:
            self.stats.from_cache += 1
            self._set_state(url, "done")
            return
        if resp.status_code == 404:
            self.stats.dead_links += 1
            self._set_state(url, "dead")
            return
        if resp.status_code >= 400:
            self.stats.errors += 1
            self._set_state(url, "error")
            return

        raw_path = self._store_raw(url, resp.text)
        self.stats.fetched += 1
        self.stats.by_class[klass] = self.stats.by_class.get(klass, 0) + 1
        self._db.execute(
            "UPDATE frontier SET state='done', etag=?, last_modified=?, "
            "content_hash=?, raw_path=? WHERE url=?",
            (resp.headers.get("etag"), resp.headers.get("last-modified"),
             hashlib.sha256(resp.text.encode()).hexdigest(), raw_path, url),
        )
        # follow links from every page (breadcrumbs on product pages surface
        # dead links; facet/home pages surface the catalogue)
        for link in self._extract_links(url, resp.text):
            if self._same_host(link):
                self._enqueue(link, depth + 1)

    def _get_with_retry(self, client, url, headers):
        delay = 0.5
        for attempt in range(self.config.max_retries):
            try:
                return client.get(url, headers=headers)
            except httpx.HTTPError:
                if attempt == self.config.max_retries - 1:
                    return None
                time.sleep(delay)
                delay *= 2
        return None

    def _extract_links(self, base_url: str, html: str) -> list[str]:
        out = []
        for node in HTMLParser(html).css("a[href]"):
            href = node.attributes.get("href")
            if not href or href.startswith(("#", "mailto:", "tel:")):
                continue
            out.append(urljoin(base_url, href))
        return out

    def _store_raw(self, url: str, text: str) -> str:
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        path = os.path.join(self.raw_dir, f"{key}.html.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
        return path

    def _set_state(self, url: str, state: str) -> None:
        self._db.execute("UPDATE frontier SET state=? WHERE url=?", (state, url))

    # --- reading back the crawl for the parser ---
    def pages(self) -> list[tuple[str, str, str]]:
        """Yield (url, klass, html) for every successfully fetched page."""
        rows = self._db.execute(
            "SELECT url, klass, raw_path FROM frontier WHERE state='done' AND raw_path IS NOT NULL"
        ).fetchall()
        out = []
        for url, klass, raw_path in rows:
            with gzip.open(raw_path, "rt", encoding="utf-8") as f:
                out.append((url, klass, f.read()))
        return out

    def close(self) -> None:
        self._db.close()
