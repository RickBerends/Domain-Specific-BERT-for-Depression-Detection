"""Read side of the snapshot contract (technical plan §2).

``SnapshotReader`` is the *only* way the chat service reaches catalogue data. It
opens a published snapshot read-only: the SQLite catalogue (with its FTS5
lexical mirror) and the serialized vector index. It never writes and never
triggers ingest.
"""

from __future__ import annotations

import json
import os
import sqlite3

from schemas import Content, Product, SnapshotRef
from schemas.snapshot_format import CATALOG_DB, SNAPSHOT_JSON, VECTORS_JSON

from chat.vectorstore import InMemoryVectorIndex, VectorHit


class SnapshotReader:
    def __init__(self, snapshot_dir: str) -> None:
        self.dir = snapshot_dir
        db_path = os.path.join(snapshot_dir, CATALOG_DB)
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"No published snapshot at {snapshot_dir!r}. "
                "Build one first: python -m ingest.seed"
            )
        # read-only, shareable across request threads
        self._db = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._vectors = InMemoryVectorIndex.load(
            os.path.join(snapshot_dir, VECTORS_JSON)
        )
        with open(os.path.join(snapshot_dir, SNAPSHOT_JSON), encoding="utf-8") as f:
            self._ref = SnapshotRef.model_validate_json(f.read())

    def ref(self) -> SnapshotRef:
        return self._ref

    # --- product access ---

    def get_product(self, slug: str) -> Product | None:
        row = self._db.execute(
            "SELECT json FROM products WHERE slug = ?", (slug,)
        ).fetchone()
        return Product.model_validate_json(row["json"]) if row else None

    def vector_search(self, embedding: list[float], top_k: int) -> list[VectorHit]:
        return self._vectors.query(embedding, top_k)

    def lexical_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """FTS5 lexical search over products. Returns (slug, score) high→low.

        bm25() returns lower-is-better; negate so callers merge on high-is-good.
        """
        match = _to_fts_query(query)
        if not match:
            return []
        rows = self._db.execute(
            """
            SELECT slug, bm25(products_fts) AS rank
            FROM products_fts
            WHERE products_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match, top_k),
        ).fetchall()
        return [(r["slug"], -float(r["rank"])) for r in rows]

    def content_search(self, query: str, top_k: int) -> list[Content]:
        match = _to_fts_query(query)
        if not match:
            return []
        rows = self._db.execute(
            """
            SELECT c.json AS json
            FROM content_fts f
            JOIN content c ON c.rowid = f.rowid_ref
            WHERE content_fts MATCH ?
            ORDER BY bm25(content_fts)
            LIMIT ?
            """,
            (match, top_k),
        ).fetchall()
        return [Content.model_validate_json(r["json"]) for r in rows]


def _to_fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 OR-query of the alphanumeric tokens.

    Avoids FTS5 syntax errors from user punctuation and widens recall (any term
    may match); relevance ordering is left to bm25 and the vector side.
    """
    tokens = [
        "".join(ch for ch in tok if ch.isalnum())
        for tok in query.lower().split()
    ]
    tokens = [t for t in tokens if len(t) > 1]
    return " OR ".join(tokens)
