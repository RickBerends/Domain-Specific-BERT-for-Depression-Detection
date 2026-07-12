"""Publish a snapshot from the hand-written seed (``python -m ingest.seed``).

This is the first-slice stand-in for a real ingest source: it validates the
seed JSON against the ``schemas`` contract and publishes it as a snapshot the
chat service can serve. Same embedder the chat side will use at query time,
selected by config (fake by default, Ollama if configured).
"""

from __future__ import annotations

import json
import os

from schemas import Content, Product

from chat.config import load_config
from chat.embeddings import build_embedder
from ingest.build_snapshot import build_snapshot

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load(name: str) -> list[dict]:
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg = load_config()
    products = [Product.model_validate(d) for d in _load("seed_products.json")]
    contents = [Content.model_validate(d) for d in _load("seed_content.json")]
    embedder = build_embedder(cfg)

    ref = build_snapshot(
        cfg.snapshot_dir,
        products,
        contents,
        embedder,
        snapshot_id="seed",
    )
    print(
        f"Published snapshot {ref.snapshot_id!r} to {cfg.snapshot_dir}\n"
        f"  products: {ref.product_count}  content: {ref.content_count}  "
        f"embedder: {cfg.embed_backend}"
    )


if __name__ == "__main__":
    main()
