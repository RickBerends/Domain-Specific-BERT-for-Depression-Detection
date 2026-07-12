"""On-disk snapshot format — part of the shared contract.

The published snapshot *is* the boundary between ingest and chat (technical
plan §2, §8). Both sides depend on this format and nothing else about each
other: ingest writes it, chat reads it. A snapshot directory contains:

    catalog.db     SQLite: products + content, each with an FTS5 mirror
    vectors.json   serialized product vector index
    snapshot.json  SnapshotRef metadata

Keeping the DDL and file names here (rather than duplicated on each side) is
what lets ingest and chat share only the ``schemas`` package.
"""

from __future__ import annotations

CATALOG_DB = "catalog.db"
VECTORS_JSON = "vectors.json"
SNAPSHOT_JSON = "snapshot.json"

# Products: one row per product; JSON columns hold the list/nested fields so a
# reader can reconstruct the full Product model. FTS5 mirror indexes the text a
# lexical query hits (names/producers/grapes are exact-match-heavy, §4.5).
DDL = """
CREATE TABLE products (
    slug         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    producer     TEXT,
    region       TEXT,
    country      TEXT,
    color_type   TEXT,
    vintage      INTEGER,
    price_cents  INTEGER,
    currency     TEXT,
    stock_status TEXT,
    language     TEXT,
    json         TEXT NOT NULL          -- full Product as JSON
);

CREATE VIRTUAL TABLE products_fts USING fts5(
    slug UNINDEXED,
    name,
    producer,
    grape_varieties,
    region,
    tasting_notes,
    food_pairing,
    tokenize = 'unicode61'
);

CREATE TABLE content (
    url          TEXT,
    title        TEXT NOT NULL,
    section      TEXT,
    language     TEXT,
    json         TEXT NOT NULL          -- full Content as JSON
);

CREATE VIRTUAL TABLE content_fts USING fts5(
    rowid_ref UNINDEXED,
    title,
    body_text,
    tokenize = 'unicode61'
);
"""
