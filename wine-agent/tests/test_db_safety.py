"""Database safety: injection payloads are inert, DB stays intact & read-only."""

from __future__ import annotations

import sqlite3

import pytest

from chat.planner import Filters
from chat.snapshot import SnapshotReader

_MALICIOUS = [
    "'; DROP TABLE products; --",
    '" OR "1"="1',
    "products_fts MATCH '*'",
    "* OR 1=1",
    "'); DELETE FROM products; --",
    "\x00 null byte",
    "NEAR(a b) OR (",  # raw FTS operators
]


@pytest.mark.parametrize("payload", _MALICIOUS)
def test_lexical_search_is_injection_safe(reader: SnapshotReader, payload: str):
    # Must not raise (no FTS syntax error, no SQL injection) and must return a list.
    results = reader.lexical_search(payload, top_k=5)
    assert isinstance(results, list)


@pytest.mark.parametrize("payload", _MALICIOUS)
def test_content_search_is_injection_safe(reader: SnapshotReader, payload: str):
    assert isinstance(reader.content_search(payload, top_k=5), list)


def test_db_intact_after_malicious_queries(reader: SnapshotReader):
    before = reader.ref().product_count
    for payload in _MALICIOUS:
        reader.lexical_search(payload, top_k=5)
        reader.content_search(payload, top_k=5)
    # a real product still resolves and the catalogue is unchanged
    assert reader.get_product("prima-luna-frascati-2025") is not None
    assert reader.ref().product_count == before


def test_connection_is_read_only(reader: SnapshotReader):
    with pytest.raises(sqlite3.OperationalError):
        reader._db.execute("DELETE FROM products")
    with pytest.raises(sqlite3.OperationalError):
        reader._db.execute("INSERT INTO products (slug, name, json) VALUES ('x','y','{}')")


def test_filters_cannot_smuggle_sql(reader: SnapshotReader):
    # Filters carry typed values (enum/str/int) through parameterized queries;
    # a string that looks like SQL is treated as a literal country, matching none.
    evil = Filters(country="France'; DROP TABLE products; --")
    assert reader.filter_products(evil, top_k=5) == []
    assert reader.get_product("prima-luna-frascati-2025") is not None
