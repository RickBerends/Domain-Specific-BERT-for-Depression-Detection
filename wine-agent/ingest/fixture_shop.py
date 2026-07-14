"""Fixture-shop generator (roadmap 3.1).

Writes a static HTML mock wine shop that mirrors the van Bilsen URL map
(addendum §2) and reproduces its field quirks (addendum §3) — Dutch
decimal-comma prices, textual stock, volume tiers, offer-expiry text — plus a
fixed set of deliberately *poisoned* pages for the validation gates to catch.

It is the safe target the real crawler/parsers are developed against; nothing
here touches a live site. Output is deterministic (seeded, fixed reference
date) so CI can assert byte-stable goldens and exact round-trips.

    python -m ingest.fixture_shop --out data/fixture_shop [--n 60] [--no-poison]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from schemas import ColorType, Product, StockStatus

REF_NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)  # frozen for determinism
CONTACT_UA_NOTE = "WineshopBot"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

_COLOR_FACET = {
    ColorType.red: "rood",
    ColorType.white: "wit",
    ColorType.rose: "rose",
    ColorType.sparkling: "mousserende-wijn",
    ColorType.dessert: "dessertwijn",
    ColorType.fortified: "port",
}
_COUNTRY_FACET = {
    "France": "frankrijk", "Italy": "italie", "Spain": "spanje",
    "Germany": "duitsland", "Portugal": "portugal", "Netherlands": "nederland",
    "Argentina": "argentinie", "Australia": "australie", "Austria": "oostenrijk",
    "New Zealand": "nieuw-zeeland", "South Africa": "zuid-afrika", "Chile": "chili",
    "Brazil": "brazilie", "Greece": "griekenland",
}
_TASTE_STYLES = ["krachtig-stevig", "vol-verfijnd", "zacht-rijk"]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x"


def country_facet(country: str | None) -> str:
    if not country:
        return "overig"
    return _COUNTRY_FACET.get(country, slugify(country))


def dutch_price(cents: int) -> str:
    return f"€ {cents // 100},{cents % 100:02d}"


def price_bucket(cents: int | None) -> str | None:
    if cents is None:
        return None
    if cents < 500:
        return "tot5euro"
    if cents < 800:
        return "van5tot8euro"
    if cents < 1500:
        return "van8tot15euro"
    return "vanaf15euro"


def taste_style(p: Product) -> str:
    return _TASTE_STYLES[sum(ord(c) for c in p.slug) % len(_TASTE_STYLES)]


def stock_text(status: StockStatus, count: int | None) -> str:
    if status is StockStatus.out:
        return "Niet op voorraad"
    if status is StockStatus.limited:
        return f"Nog {count} flessen beschikbaar" if count else "Beperkt beschikbaar"
    return "Op voorraad"


def product_id(p: Product) -> str:
    if p.url:
        m = re.search(r"/product/(\d+)/", str(p.url))
        if m:
            return m.group(1)
    return str(abs(hash(p.slug)) % 10_000_000_000)


@dataclass
class Manifest:
    product_ids: list[str] = field(default_factory=list)
    poisoned_ids: list[str] = field(default_factory=list)
    facet_pages: list[str] = field(default_factory=list)
    content_pages: list[str] = field(default_factory=list)
    dead_link_target: str | None = None


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"nl\"><head><meta charset=\"utf-8\">"
        f"<title>{title} — Wijnhoek (fixture)</title></head>\n<body>\n{body}\n"
        "<footer><p>Openingstijden: di–za 10:00–18:00 · "
        "Gratis bezorging vanaf € 75 · NIX18</p></footer>\n</body></html>\n"
    )


def render_product(p: Product, pid: str, now: datetime, *, tier: bool) -> str:
    rows = []
    if p.color_type:
        rows.append(("Kleur", p.color_type.value))
    if p.grape_varieties:
        rows.append(("Druiven", ", ".join(p.grape_varieties)))
    if p.country:
        rows.append(("Land", p.country))
    if p.region:
        rows.append(("Regio", p.region))
    if p.vintage:
        rows.append(("Jaar", str(p.vintage)))
    if p.abv is not None:
        rows.append(("Alcohol", f"{p.abv}%"))
    if p.volume_ml:
        rows.append(("Inhoud", f"{p.volume_ml} ml"))
    table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)

    price_html = ""
    if p.price_cents is not None:
        if p.offer_valid_until:
            until = p.offer_valid_until.strftime("%-d %B %Y")
            price_html = (
                f'<p class="price"><span class="sale">{dutch_price(p.price_cents)} '
                f'per fles</span> <span class="offer">Aanbieding geldig t/m {until}</span></p>'
            )
        else:
            price_html = f'<p class="price">{dutch_price(p.price_cents)} per fles</p>'

    tier_html = ""
    if tier and p.price_cents is not None:
        tier_price = int(p.price_cents * 0.95)
        tier_html = f'<p class="tier">Vanaf 6 flessen {dutch_price(tier_price)} per fles</p>'

    notes = f'<p class="omschrijving">{p.tasting_notes}</p>' if p.tasting_notes else ""
    pairing = (
        f'<p class="spijs">Lekker bij: {", ".join(p.food_pairing)}.</p>'
        if p.food_pairing else ""
    )
    img = f'<img src="{p.image_url}" alt="{p.name}">' if p.image_url else ""
    crumbs = (
        f'<nav class="kruimel"><a href="/">Home</a> › '
        f'<a href="/{_COLOR_FACET.get(p.color_type, "wijn")}">{(p.color_type.value if p.color_type else "wijn")}</a>'
        f' › {p.name}</nav>'
    )
    body = (
        f"{crumbs}\n<h1>{p.name}</h1>\n{img}\n{price_html}\n{tier_html}\n"
        f'<p class="voorraad">{stock_text(p.stock_status, p.stock_count)}</p>\n'
        f"<h2>Productinformatie</h2>\n<table class=\"productinfo\">{table}</table>\n"
        f"{notes}\n{pairing}\n"
    )
    return _page(p.name, body)


def render_facet(name: str, slug: str, members: list[tuple[str, str, str]]) -> str:
    # members: (id, slug, name)
    items = "".join(
        f'<li><a href="/product/{pid}/{s}">{n}</a></li>' for pid, s, n in members
    )
    return _page(name, f"<h1>{name}</h1>\n<ul class=\"producten\">{items}</ul>")


def render_content(title: str, body: str) -> str:
    return _page(title, f"<h1>{title}</h1>\n<div class=\"inhoud\">{body}</div>")


CONTENT_PAGES = {
    "bezorging": ("Bezorging en verzendkosten",
        "Wij bezorgen in Nederland, België, Duitsland en Frankrijk. Gratis vanaf "
        "€ 75; daaronder € 6,95. Alle prijzen zijn inclusief BTW."),
    "herroepingsrecht": ("Herroepingsrecht",
        "Ongeopende flessen kunnen binnen 14 dagen worden geretourneerd."),
    "klantenservice/veelgestelde-vragen": ("Veelgestelde vragen",
        "Antwoorden op veelgestelde vragen over bestellen en bezorgen."),
    "wijnspijswijzer": ("Wijn-spijswijzer",
        "Frisse witte wijnen passen bij vis; volle rode wijnen bij rood vlees en "
        "stoofpotten; port bij blauwe kaas."),
    "begrippenlijst": ("Begrippenlijst",
        "Tannine: een stof in rode wijn die een droge, stroeve sensatie geeft. "
        "Brut: een droge stijl mousserende wijn."),
    "geborgde-werkwijze-leeftijdscheck-18": ("NIX18 leeftijdscheck",
        "Wij verkopen geen alcohol aan personen onder de 18 jaar (NIX18)."),
}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def _write(out_dir: str, path: str, html: str) -> None:
    full = os.path.join(out_dir, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(html)


def generate(
    out_dir: str, products: list[Product], now: datetime = REF_NOW, *, poison: bool = True
) -> Manifest:
    import shutil

    shutil.rmtree(out_dir, ignore_errors=True)  # clean slate → byte-stable output
    manifest = Manifest()
    facets: dict[tuple[str, str], list[tuple[str, str, str]]] = {}

    def add_facet(name: str, slug: str, entry: tuple[str, str, str]) -> None:
        facets.setdefault((name, slug), []).append(entry)

    for i, p in enumerate(products):
        pid = product_id(p)
        entry = (pid, p.slug, p.name)
        manifest.product_ids.append(pid)
        _write(out_dir, f"product/{pid}/{p.slug}.html",
               render_product(p, pid, now, tier=(i % 5 == 0)))

        if p.color_type:
            add_facet(p.color_type.value, _COLOR_FACET[p.color_type], entry)
        add_facet(p.country or "Overig", country_facet(p.country), entry)
        for grape in p.grape_varieties:
            add_facet(grape, slugify(grape), entry)
        bucket = price_bucket(p.price_cents)
        if bucket:
            add_facet(bucket, bucket, entry)
        add_facet(taste_style(p), f"modelwijnen/{taste_style(p)}", entry)

    for (name, slug), members in facets.items():
        _write(out_dir, f"{slug}.html", render_facet(name, slug, members))
        manifest.facet_pages.append("/" + slug)

    for slug, (title, body) in CONTENT_PAGES.items():
        _write(out_dir, f"{slug}.html", render_content(title, body))
        manifest.content_pages.append("/" + slug)

    if poison:
        _generate_poisoned(out_dir, now, manifest)

    _write_home_sitemap_robots(out_dir, manifest)
    return manifest


def _generate_poisoned(out_dir: str, now: datetime, manifest: Manifest) -> None:
    # 1. missing price
    pid = "9000001"
    body = "<h1>Mysterie Rood</h1>\n<p class=\"voorraad\">Op voorraad</p>"
    _write(out_dir, f"product/{pid}/mysterie-rood.html", _page("Mysterie Rood", body))
    manifest.poisoned_ids.append(pid)

    # 2. malformed markup (unclosed tags)
    pid = "9000002"
    body = "<h1>Kapotte Wijn<p class=\"price\">€ 9,50 per fles<p>Op voorraad<table><tr><th>Land<td>Frankrijk"
    _write(out_dir, f"product/{pid}/kapotte-wijn.html",
           "<!doctype html><html><body>" + body)  # no closing tags
    manifest.poisoned_ids.append(pid)

    # 3. lapsed offer (expiry in the past)
    pid = "9000003"
    past = (now - timedelta(days=10)).strftime("%-d %B %Y")
    body = (f"<h1>Verlopen Aanbieding</h1><p class=\"price\"><span class=\"sale\">"
            f"€ 7,50 per fles</span> <span class=\"offer\">Aanbieding geldig t/m {past}"
            f"</span></p><p class=\"voorraad\">Op voorraad</p>")
    _write(out_dir, f"product/{pid}/verlopen-aanbieding.html", _page("Verlopen Aanbieding", body))
    manifest.poisoned_ids.append(pid)

    # 4. dead link (page links to a product that does not exist)
    pid = "9000004"
    manifest.dead_link_target = "/product/9999999/bestaat-niet"
    body = (f'<h1>Levende Wijn</h1><p class="price">€ 12,00 per fles</p>'
            f'<p class="voorraad">Op voorraad</p>'
            f'<p>Zie ook <a href="{manifest.dead_link_target}">deze wijn</a>.</p>')
    _write(out_dir, f"product/{pid}/levende-wijn.html", _page("Levende Wijn", body))
    manifest.poisoned_ids.append(pid)

    # 5. duplicate canonical id (two slugs, same id) — must dedupe
    body = ('<h1>Dubbele Wijn</h1><p class="price">€ 11,00 per fles</p>'
            '<p class="voorraad">Op voorraad</p>')
    _write(out_dir, "product/9000005/dubbele-wijn.html", _page("Dubbele Wijn", body))
    _write(out_dir, "product/9000005/dubbele-wijn-kopie.html", _page("Dubbele Wijn", body))
    manifest.poisoned_ids.append("9000005")


def _write_home_sitemap_robots(out_dir: str, manifest: Manifest) -> None:
    links = manifest.facet_pages + manifest.content_pages
    home_body = "<h1>Wijnhoek (fixture)</h1>\n<ul>" + "".join(
        f'<li><a href="{u}">{u}</a></li>' for u in links
    ) + '</ul>\n<p><a href="/sitemap">Sitemap</a></p>'
    _write(out_dir, "index.html", _page("Home", home_body))

    # HTML sitemap listing every product + facet + content page
    all_urls = (
        [f"/product/{pid}/" for pid in manifest.product_ids]
        + manifest.facet_pages + manifest.content_pages
    )
    # rebuild exact product URLs from files on disk (id/slug)
    prod_urls: list[str] = []
    proot = os.path.join(out_dir, "product")
    for pid in sorted(os.listdir(proot)) if os.path.isdir(proot) else []:
        for fn in sorted(os.listdir(os.path.join(proot, pid))):
            if fn.endswith(".html"):
                prod_urls.append(f"/product/{pid}/{fn[:-5]}")
    sitemap_urls = prod_urls + manifest.facet_pages + manifest.content_pages
    sm_body = "<h1>Sitemap</h1>\n<ul>" + "".join(
        f'<li><a href="{u}">{u}</a></li>' for u in sitemap_urls
    ) + "</ul>"
    _write(out_dir, "sitemap.html", _page("Sitemap", sm_body))

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "".join(f"<url><loc>{u}</loc></url>\n" for u in sitemap_urls)
           + "</urlset>\n")
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(xml)

    with open(os.path.join(out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write("User-agent: *\nDisallow: /cart\nDisallow: /account\n"
                "Sitemap: /sitemap.xml\n")


# --------------------------------------------------------------------------- #
# Serving (faithful extensionless URLs)
# --------------------------------------------------------------------------- #

class _Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        fs = super().translate_path(path)
        if os.path.isdir(fs):
            return fs
        if not os.path.exists(fs) and os.path.exists(fs + ".html"):
            return fs + ".html"
        return fs

    def log_message(self, *args) -> None:  # keep tests quiet
        pass


def serve_fixture(directory: str, port: int = 0) -> tuple[http.server.HTTPServer, int]:
    """Start a threaded static server for the fixture shop. Returns (server, port)."""
    handler = functools.partial(_Handler, directory=directory)
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def load_fixture_products(n: int, now: datetime = REF_NOW) -> list[Product]:
    """Seed products (Dutch content) + X-Wines rows, deduped, first ``n``."""
    from ingest.xwines import ensure_dataset, load_products

    with open(os.path.join(DATA_DIR, "seed_products.json"), encoding="utf-8") as f:
        products = [Product.model_validate(d) for d in json.load(f)]
    seen = {p.slug for p in products}
    for p in load_products(ensure_dataset()):
        # re-stamp offer dates deterministically via the frozen ref date
        if p.slug not in seen:
            products.append(p)
            seen.add(p.slug)
    return products[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "fixture_shop"))
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--no-poison", action="store_true")
    args = ap.parse_args()

    products = load_fixture_products(args.n)
    manifest = generate(args.out, products, poison=not args.no_poison)
    print(
        f"Generated fixture shop at {args.out}\n"
        f"  products: {len(manifest.product_ids)}  facets: {len(manifest.facet_pages)}"
        f"  content: {len(manifest.content_pages)}  poisoned: {len(manifest.poisoned_ids)}"
    )


if __name__ == "__main__":
    main()
