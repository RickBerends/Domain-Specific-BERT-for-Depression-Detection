"""HTML → snapshot models (roadmap 3.3, technical plan §4.3).

A site-shaped adapter for the van Bilsen page structure (which the fixture shop
reproduces). Two-pass, **facet-first** (addendum §3): listing pages give
authoritative color/country/taste-style/price-bucket membership per product id;
product pages then supply the rest (name, price block incl. sale + offer
expiry, the Productinformatie table, prose, stock text, image, tiers). Bad
pages produce a recorded ``ParseError`` — never a crash — so the validation
gates decide what to publish.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from schemas import ColorType, Content, Language, Product, PriceTier, StockStatus

from ingest.fixture_shop import _COLOR_FACET, _COUNTRY_FACET, CONTENT_PAGES

# Invert the site's facet vocabulary (same profile the fixture emits).
_FACET_COLOR = {slug: color for color, slug in _COLOR_FACET.items()}
_FACET_COUNTRY = {slug: name for name, slug in _COUNTRY_FACET.items()}
_PRICE_BUCKETS = {"tot5euro", "van5tot8euro", "van8tot15euro", "vanaf15euro"}

_PRODUCT_URL = re.compile(r"/product/(\d+)/([^/]+)")
_PRICE = re.compile(r"€\s*(\d+),(\d{2})")
_TIER = re.compile(r"[Vv]anaf\s+(\d+)\s+flessen\s+€\s*(\d+),(\d{2})")
_STOCK_N = re.compile(r"[Nn]og\s+(\d+)")
_OFFER = re.compile(r"t/m\s+(\d{1,2}\s+\w+\s+\d{4})")
_VINTAGE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class ParseError:
    url: str
    reason: str


@dataclass
class ParseResult:
    products: list[Product] = field(default_factory=list)
    contents: list[Content] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Normalizers (unit-tested directly)
# --------------------------------------------------------------------------- #

def parse_dutch_price(text: str) -> int | None:
    m = _PRICE.search(text or "")
    return int(m.group(1)) * 100 + int(m.group(2)) if m else None


def parse_stock(text: str) -> tuple[StockStatus, int | None]:
    t = (text or "").lower()
    if "niet op voorraad" in t or "uitverkocht" in t:
        return StockStatus.out, None
    if "nog" in t:
        m = _STOCK_N.search(t)
        return StockStatus.limited, (int(m.group(1)) if m else None)
    if "beperkt" in t:
        return StockStatus.limited, None
    return StockStatus.in_stock, None


def parse_tiers(text: str) -> list[PriceTier]:
    return [
        PriceTier(min_qty=int(q), price_cents=int(e) * 100 + int(c))
        for q, e, c in _TIER.findall(text or "")
    ]


def parse_offer_date(text: str) -> datetime | None:
    m = _OFFER.search(text or "")
    if not m:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def vintage_from_slug(slug: str) -> int | None:
    years = _VINTAGE.findall(slug or "")
    # findall returns the group ("19"/"20"); re-extract full years
    full = re.findall(r"\b((?:19|20)\d{2})\b", slug or "")
    return int(full[-1]) if full else None


# --------------------------------------------------------------------------- #
# Facet membership (pass 1)
# --------------------------------------------------------------------------- #

@dataclass
class Membership:
    color: dict[str, ColorType] = field(default_factory=dict)
    country: dict[str, str] = field(default_factory=dict)


def _facet_slug(url: str) -> str:
    return urlparse(url).path.strip("/")


def build_membership(category_pages: list[tuple[str, str]]) -> Membership:
    """category_pages: (url, html). Records color/country per product id."""
    m = Membership()
    for url, html in category_pages:
        slug = _facet_slug(url)
        color = _FACET_COLOR.get(slug)
        country = _FACET_COUNTRY.get(slug)
        if color is None and country is None:
            continue
        for node in HTMLParser(html).css("a[href]"):
            pm = _PRODUCT_URL.search(node.attributes.get("href") or "")
            if not pm:
                continue
            pid = pm.group(1)
            if color is not None:
                m.color[pid] = color
            if country is not None:
                m.country[pid] = country
    return m


# --------------------------------------------------------------------------- #
# Product & content extraction (pass 2)
# --------------------------------------------------------------------------- #

def _text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    return node.text(strip=True) if node else None


def _info_table(tree: HTMLParser) -> dict[str, str]:
    out: dict[str, str] = {}
    table = tree.css_first("table.productinfo")
    if not table:
        return out
    for tr in table.css("tr"):
        th, td = tr.css_first("th"), tr.css_first("td")
        if th and td:
            out[th.text(strip=True).lower()] = td.text(strip=True)
    return out


def parse_product(url: str, html: str, membership: Membership) -> Product:
    m = _PRODUCT_URL.search(url)
    if not m:
        raise ValueError("not a product URL")
    pid, slug = m.group(1), m.group(2)
    tree = HTMLParser(html)

    name = _text(tree, "h1")
    if not name:
        raise ValueError("no <h1> product name")

    info = _info_table(tree)
    price_text = _text(tree, ".price") or ""
    color = membership.color.get(pid) or _color_from_text(info.get("kleur"))
    country = membership.country.get(pid) or info.get("land")

    grapes = [g.strip() for g in (info.get("druiven", "").split(",")) if g.strip()]
    pairing_text = _text(tree, ".spijs") or ""
    pairing = [
        s.strip(" .") for s in re.sub(r"(?i)lekker bij:", "", pairing_text).split(",")
        if s.strip(" .")
    ]

    return Product(
        slug=slug,
        name=name,
        grape_varieties=grapes,
        region=info.get("regio"),
        country=country,
        color_type=color,
        vintage=_int(info.get("jaar")) or vintage_from_slug(slug),
        abv=_float(info.get("alcohol")),
        volume_ml=_int(info.get("inhoud")),
        price_cents=parse_dutch_price(price_text),
        price_tiers=parse_tiers(_text(tree, ".tier") or ""),
        offer_valid_until=parse_offer_date(_text(tree, ".offer") or ""),
        stock_status=parse_stock(_text(tree, ".voorraad") or "")[0],
        stock_count=parse_stock(_text(tree, ".voorraad") or "")[1],
        tasting_notes=_text(tree, ".omschrijving"),
        food_pairing=pairing,
        language=Language.nl,
        image_url=_img(tree, url),
        url=url,
        scraped_at=datetime.now(timezone.utc),
    )


def parse_content(url: str, html: str) -> Content:
    tree = HTMLParser(html)
    title = _text(tree, "h1") or urlparse(url).path.strip("/")
    body = _text(tree, ".inhoud") or ""
    slug = urlparse(url).path.strip("/")
    section = "policy" if slug in ("bezorging", "herroepingsrecht") else "guide"
    return Content(url=url, title=title, section=section, body_text=body,
                   language=Language.nl)


def parse_all(pages: list[tuple[str, str, str]]) -> ParseResult:
    """pages: (url, klass, html). Facet-first, dedup products by canonical id."""
    result = ParseResult()
    category = [(u, h) for (u, k, h) in pages if k == "category"]
    membership = build_membership(category)

    seen_ids: set[str] = set()
    for url, klass, html in pages:
        try:
            if klass == "product":
                pm = _PRODUCT_URL.search(url)
                pid = pm.group(1) if pm else None
                if pid and pid in seen_ids:
                    continue  # duplicate canonical id → keep first only
                product = parse_product(url, html, membership)
                if pid:
                    seen_ids.add(pid)
                result.products.append(product)
            elif klass == "content":
                result.contents.append(parse_content(url, html))
        except Exception as exc:  # noqa: BLE001 — resilience is the point
            result.errors.append(ParseError(url=url, reason=f"{type(exc).__name__}: {exc}"))
    return result


# --- small helpers ---

def _color_from_text(value: str | None) -> ColorType | None:
    if not value:
        return None
    try:
        return ColorType(value.strip().lower())
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group()) if m else None


def _float(value: str | None) -> float | None:
    if not value:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", value)
    return float(m.group().replace(",", ".")) if m else None


def _img(tree: HTMLParser, base_url: str) -> str | None:
    node = tree.css_first("img[src]")
    if not node:
        return None
    src = node.attributes.get("src")
    if src and src.startswith("http"):
        return src
    return None
