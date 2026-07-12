"""X-Wines dataset loader (data-acquisition plan §2.1, §2.3).

Maps X-Wines rows onto the ``Product`` contract and synthesizes the commerce
fields the dataset lacks (price, stock, offers) **deterministically** — every
synthetic value is derived from the WineID hash, so repeated runs publish
byte-identical catalogues and tests can assert exact values.

Run: ``python -m ingest.xwines``

Downloads the 100-wine Test CSV from the X-Wines GitHub repo (cached in
``data/external/``). The 1k Slim version is only distributed via Google Drive;
if you download ``XWines_Slim_1K_wines.csv`` manually into ``data/external/``,
it is picked up automatically. Merges the hand-written seed (which carries the
NL wines and the content pages) and publishes one snapshot.

Citation: de Azambuja, R.X.; Morais, A.J.; Filipe, V. X-Wines: A Wine Dataset
for Recommender Systems and Machine Learning. Big Data Cogn. Comput. 2023, 7, 20.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from schemas import ColorType, Language, Product, StockStatus

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
EXTERNAL_DIR = os.path.join(DATA_DIR, "external")

TEST_CSV_URL = (
    "https://raw.githubusercontent.com/rogerioxavier/X-Wines/main/"
    "Dataset/last/XWines_Test_100_wines.csv"
)
SLIM_FILENAME = "XWines_Slim_1K_wines.csv"
TEST_FILENAME = "xwines_test_100.csv"

_TYPE_MAP = {
    "red": ColorType.red,
    "white": ColorType.white,
    "rosé": ColorType.rose,
    "rose": ColorType.rose,
    "sparkling": ColorType.sparkling,
    "dessert": ColorType.dessert,
    "dessert/port": ColorType.fortified,
}

# price bands in cents: (base, spread) keyed by color type
_PRICE_BANDS = {
    ColorType.red: (900, 2400),
    ColorType.white: (800, 1800),
    ColorType.rose: (800, 1200),
    ColorType.sparkling: (1000, 3000),
    ColorType.dessert: (1100, 2200),
    ColorType.fortified: (1300, 2800),
}


def _stable_int(wine_id: str, salt: str, mod: int) -> int:
    """Deterministic pseudo-random int in [0, mod) derived from the wine id."""
    digest = hashlib.sha256(f"{wine_id}:{salt}".encode()).hexdigest()
    return int(digest[:12], 16) % mod


def _parse_list(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return [raw.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "wine"


def _pick_vintage(raw: str, color: ColorType | None) -> int | None:
    vintages = [v for v in _parse_list(raw) if v.isdigit()]
    years = sorted((int(v) for v in vintages), reverse=True)
    years = [y for y in years if 1950 <= y <= 2026]
    if not years:
        return None
    # sparkling & fortified are often non-vintage blends; keep NV for those
    if color in (ColorType.sparkling, ColorType.fortified):
        return None
    return years[0]


def _tasting_note(body: str, acidity: str, grapes: list[str], color: ColorType | None) -> str:
    """X-Wines has no prose; render its structured facts as a short note."""
    bits: list[str] = []
    if body:
        bits.append(body.replace("-bodied", "-bodied").lower())
    if acidity:
        bits.append(f"{acidity.lower()} acidity")
    style = " with ".join(bits) if bits else "balanced"
    grape_part = f" made from {', '.join(grapes)}" if grapes else ""
    color_word = color.value if color else "wine"
    return f"A {style} {color_word}{grape_part}."


def row_to_product(row: dict[str, str], now: datetime | None = None) -> Product:
    now = now or datetime.now(timezone.utc)
    wine_id = row["WineID"].strip()
    color = _TYPE_MAP.get(row.get("Type", "").strip().lower())
    grapes = _parse_list(row.get("Grapes", ""))
    pairing = _parse_list(row.get("Harmonize", ""))
    vintage = _pick_vintage(row.get("Vintages", ""), color)

    base, spread = _PRICE_BANDS.get(color, (900, 2000))
    price_cents = base + _stable_int(wine_id, "price", spread)
    price_cents -= price_cents % 5  # shop-style ,x0/,x5 endings

    stock_roll = _stable_int(wine_id, "stock", 100)
    if stock_roll < 5:
        stock_status, stock_count = StockStatus.out, 0
    elif stock_roll < 15:
        stock_status, stock_count = StockStatus.limited, 1 + _stable_int(wine_id, "count", 9)
    else:
        stock_status, stock_count = StockStatus.in_stock, None

    offer_valid_until = None
    if _stable_int(wine_id, "offer", 100) < 8:  # ~8% of wines on offer
        price_cents = int(price_cents * 0.85)
        offer_valid_until = (now + timedelta(days=30)).replace(
            hour=23, minute=59, second=59, microsecond=0
        )

    name = row["WineName"].strip()
    slug = f"{_slugify(name)}-{wine_id}"
    return Product(
        slug=slug,
        name=name,
        producer=row.get("WineryName", "").strip() or None,
        grape_varieties=grapes,
        region=row.get("RegionName", "").strip() or None,
        country=row.get("Country", "").strip() or None,
        color_type=color,
        vintage=vintage,
        abv=float(row["ABV"]) if row.get("ABV", "").strip() else None,
        volume_ml=750,
        price_cents=price_cents,
        stock_status=stock_status,
        stock_count=stock_count,
        offer_valid_until=offer_valid_until,
        tasting_notes=_tasting_note(
            row.get("Body", ""), row.get("Acidity", ""), grapes, color
        ),
        food_pairing=pairing,
        language=Language.en,
        url=f"https://fixtureshop.example/product/{wine_id}/{_slugify(name)}",
        scraped_at=now,
    )


def load_products(csv_path: str) -> list[Product]:
    with open(csv_path, encoding="utf-8") as f:
        return [row_to_product(row) for row in csv.DictReader(f)]


def ensure_dataset() -> str:
    """Return the path to the best locally available X-Wines CSV.

    Prefers a manually downloaded Slim file; otherwise downloads (and caches)
    the Test file from GitHub.
    """
    slim = os.path.join(EXTERNAL_DIR, SLIM_FILENAME)
    if os.path.exists(slim):
        return slim
    test = os.path.join(EXTERNAL_DIR, TEST_FILENAME)
    if not os.path.exists(test):
        os.makedirs(EXTERNAL_DIR, exist_ok=True)
        resp = httpx.get(TEST_CSV_URL, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        with open(test, "wb") as f:
            f.write(resp.content)
    return test


def main() -> None:
    import json

    from schemas import Content

    from chat.config import load_config
    from chat.embeddings import build_embedder
    from ingest.build_snapshot import build_snapshot

    cfg = load_config()
    csv_path = ensure_dataset()
    products = load_products(csv_path)

    # keep the hand-written seed: it carries the NL wines and all content pages
    with open(os.path.join(DATA_DIR, "seed_products.json"), encoding="utf-8") as f:
        seed_products = [Product.model_validate(d) for d in json.load(f)]
    with open(os.path.join(DATA_DIR, "seed_content.json"), encoding="utf-8") as f:
        contents = [Content.model_validate(d) for d in json.load(f)]

    seen = {p.slug for p in products}
    products += [p for p in seed_products if p.slug not in seen]

    ref = build_snapshot(
        cfg.snapshot_dir,
        products,
        contents,
        build_embedder(cfg),
        snapshot_id="xwines",
    )
    print(
        f"Published snapshot {ref.snapshot_id!r} from {os.path.basename(csv_path)}\n"
        f"  products: {ref.product_count}  content: {ref.content_count}  "
        f"embedder: {cfg.embed_backend}"
    )


if __name__ == "__main__":
    main()
