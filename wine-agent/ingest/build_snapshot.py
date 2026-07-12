"""Write side of the snapshot contract.

Takes validated ``Product``/``Content`` models and publishes a snapshot
directory (SQLite catalogue + FTS5 mirrors + vector index + SnapshotRef). This
is the seam every future ingest source plugs into — dataset loaders, the
fixture-shop generator, and eventually the van Bilsen crawler all end here.

The embedder is injected so the same embeddings backend is used at index time
and query time (a fake-vs-Ollama mismatch would silently wreck retrieval).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from schemas import Content, Product, SnapshotRef
from schemas.snapshot_format import CATALOG_DB, DDL, SNAPSHOT_JSON, VECTORS_JSON

from chat.embeddings import Embedder
from chat.vectorstore import InMemoryVectorIndex


def build_snapshot(
    snapshot_dir: str,
    products: list[Product],
    contents: list[Content],
    embedder: Embedder,
    snapshot_id: str | None = None,
) -> SnapshotRef:
    os.makedirs(snapshot_dir, exist_ok=True)
    db_path = os.path.join(snapshot_dir, CATALOG_DB)
    if os.path.exists(db_path):
        os.remove(db_path)

    db = sqlite3.connect(db_path)
    try:
        db.executescript(DDL)
        _write_products(db, products, snapshot_dir, embedder)
        _write_content(db, contents)
        db.commit()
    finally:
        db.close()

    ref = SnapshotRef(
        snapshot_id=snapshot_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        created_at=datetime.now(timezone.utc),
        published=True,
        product_count=len(products),
        content_count=len(contents),
    )
    with open(os.path.join(snapshot_dir, SNAPSHOT_JSON), "w", encoding="utf-8") as f:
        f.write(ref.model_dump_json(indent=2))
    return ref


def _write_products(
    db: sqlite3.Connection,
    products: list[Product],
    snapshot_dir: str,
    embedder: Embedder,
) -> None:
    cards = [p.retrieval_card() for p in products]
    embeddings = embedder.embed(cards) if cards else []
    index = InMemoryVectorIndex()

    for product, embedding in zip(products, embeddings):
        db.execute(
            """INSERT INTO products
               (slug, name, producer, region, country, color_type, vintage,
                price_cents, currency, stock_status, language, json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                product.slug,
                product.name,
                product.producer,
                product.region,
                product.country,
                product.color_type.value if product.color_type else None,
                product.vintage,
                product.price_cents,
                product.currency,
                product.stock_status.value,
                product.language.value,
                product.model_dump_json(),
            ),
        )
        db.execute(
            """INSERT INTO products_fts
               (slug, name, producer, grape_varieties, region, tasting_notes, food_pairing)
               VALUES (?,?,?,?,?,?,?)""",
            (
                product.slug,
                product.name,
                product.producer or "",
                " ".join(product.grape_varieties),
                product.region or "",
                product.tasting_notes or "",
                " ".join(product.food_pairing),
            ),
        )
        index.add(
            product.slug,
            embedding,
            {
                "slug": product.slug,
                "color_type": product.color_type.value if product.color_type else None,
                "country": product.country,
                "price_cents": product.price_cents,
                "language": product.language.value,
            },
        )

    index.save(os.path.join(snapshot_dir, VECTORS_JSON))


def _write_content(db: sqlite3.Connection, contents: list[Content]) -> None:
    for content in contents:
        cur = db.execute(
            "INSERT INTO content (url, title, section, language, json) VALUES (?,?,?,?,?)",
            (
                str(content.url) if content.url else None,
                content.title,
                content.section,
                content.language.value,
                content.model_dump_json(),
            ),
        )
        db.execute(
            "INSERT INTO content_fts (rowid_ref, title, body_text) VALUES (?,?,?)",
            (cur.lastrowid, content.title, content.body_text),
        )
