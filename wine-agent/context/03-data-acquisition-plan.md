# Data Acquisition Plan — building without the client

How to get catalog data, wine knowledge, and scrape targets **today**, without crawling
wijnkoperijvanbilsen.nl. Companion to [`01-technical-plan.md`](./01-technical-plan.md)
(architecture) and [`02-vanbilsen-site-addendum.md`](./02-vanbilsen-site-addendum.md)
(client-site findings).

## 1. Purpose and principle

The client site is off-limits until we have explicit consent, and we don't want the
project blocked on that. The architecture already gives us the way out: the **snapshot
contract** (§2 of the technical plan). The chat service reads only a published snapshot
of `Product` and `Content` rows — it does not care where they came from. So:

> **Any source that emits valid `Product`/`Content` snapshot rows is a legitimate
> ingest adapter.** A CSV dataset, a Wikipedia dump, a local mock site, and the real
> client crawler are interchangeable behind that contract.

Development order:

1. **Datasets** — instant catalog, zero scraping (§2 below)
2. **Fixture shop** — a local mock site to develop the actual crawler against (§4)
3. **Open real-web sources** — integration checks against genuinely open APIs (§5)
4. **Van Bilsen** — added last, as just another adapter, only after consent

## 2. Catalog datasets

### 2.1 X-Wines — primary structured catalog

[github.com/rogerioxavier/X-Wines](https://github.com/rogerioxavier/X-Wines) ·
[paper (MDPI BDCC 2023)](https://www.mdpi.com/2504-2289/7/1/20)

- ~100,646 wine labels, 21M user ratings (1–5), 30k wineries, 62 countries.
- Attributes we care about: wine type (red/white/rosé/sparkling/dessert/fortified),
  grape varieties, region + country, ABV, body, acidity, vintages, and — the standout —
  **`Harmonize`: a food-pairing list per wine** ("beef", "pasta", "soft cheese", …).
- Open Data Commons license (ODC-BY / ODbL family) — safe for our use.
- Ships in three sizes: **Test (100 wines)** for unit tests, **Slim (1k wines, 150k
  ratings)** for development — the right default — and Full for later scale testing.

### 2.2 WineEnthusiast reviews — tasting-note prose

[kaggle.com/datasets/zynicide/wine-reviews](https://www.kaggle.com/datasets/zynicide/wine-reviews)

- ~130k reviews: sommelier-written `description` (taste, smell, structure), plus
  points, price, country/province/region, variety, winery, designation.
- This is what X-Wines lacks: **realistic free-text tasting notes**, exactly the prose
  the RAG pipeline must chunk, embed, and stay grounded in.
- License: CC BY-NC-SA 4.0 — fine for internal development and evals; not for
  redistribution in a commercial product. The client's own descriptions replace it in
  production anyway.

### 2.3 Join strategy and schema mapping

Two usable modes — pick per test scenario, don't over-engineer a perfect join:

- **Standalone:** treat each WineEnthusiast review row as a product (name = title,
  tasting_notes = description). Simplest; great for retrieval tests.
- **Joined:** X-Wines row for structured fields + a WineEnthusiast description attached
  by (variety, country/region) match. Used for the fixture shop, where we want both
  clean facets *and* prose on one page.

Mapping onto the `Product` schema (§4.3 of the technical plan):

| Product field | X-Wines | WineEnthusiast | Missing → synthesize |
|---|---|---|---|
| `name`, `producer` | WineName, WineryName | title, winery | — |
| `grape_varieties[]` | Grapes | variety | — |
| `region`, `country` | RegionName, Country | region_1/province, country |— |
| `color_type` | Type | (infer from variety) | — |
| `vintage` | Vintages | (parse from title) | — |
| `abv` | ABV | — | — |
| `tasting_notes` | — | description | — |
| `food_pairing` | Harmonize | — | — |
| `price`, `currency` | — | price (USD) | convert/randomize to EUR |
| `in_stock`, stock count | — | — | **synthetic** |
| price tiers, `offer_valid_until` | — | — | **synthetic** |
| `url`, `slug`, `image_url` | — | — | fixture-shop generated |

The synthetic fields matter: the addendum showed the client site has volume tiers,
textual stock, and expiring offers. Generating those deterministically (seeded RNG)
means the parsing/validation logic for them is testable long before the real crawl.

**Rejected:** UCI Wine Quality dataset — physicochemical measurements only
(pH, sulphates…), no names or text; useless for a conversational catalog.

## 3. Wine-knowledge sources (EN + NL)

For the `Content` side of the schema — the role van Bilsen's *begrippenlijst*
(glossary), *wijnspijswijzer* (pairing guide), and info pages will play in production:

- **Wikipedia (EN + NL)** via the official REST API or dumps. Curated seed list of
  articles: grape varieties, wine regions, wine styles, viticulture terms, and the NL
  equivalents (nl.wikipedia has solid coverage of *druivenrassen* and *wijnstreken*).
  Chunked per the technical plan §4.5 (~400 tokens, 15% overlap), each chunk tagged
  with `language`. CC BY-SA, API explicitly open — respect the API etiquette
  (User-Agent, request rate) rather than robots.txt guesswork.
- **Wikidata** for structured facts: grape ↔ region ↔ country relations with
  **multilingual labels** — this is the EN↔NL terminology bridge (e.g. *druif* names
  matching English grape names) that query planning can use for cross-language filters.
- **Open Food Facts** (open API, ODbL) — real-world wine products with barcodes and
  label data. Secondary: useful as messy-real-data input for parser robustness, not as
  the primary catalog.

## 4. Fixture shop — the primary scrape target

A small **static-HTML mock wine shop**, generated from ~200 joined dataset rows
(§2.3), served locally with `python -m http.server`. This is what the crawler is
actually developed against.

**Mirrors the van Bilsen URL map** (addendum §2) so the URL classifier, facet-first
crawling, and extractors transfer directly:

- `/product/{id}/{slug}` product pages, canonical redirect from `/product/{id}`
- Facet listings: `/rood`, `/wit`, `/frankrijk`, `/chardonnay`, price buckets
  (`/tot5euro` … `/vanaf15euro`), taste styles `/modelwijnen/{stijl}`
- Content pages: FAQ, bezorging, herroepingsrecht, a glossary, a pairing guide
- An HTML `/sitemap` and a `robots.txt`

**Reproduces the client's field quirks on purpose** (addendum §3): Dutch
decimal-comma prices ("13,95 per fles"), textual stock ("Nog 6 flessen beschikbaar",
"Beperkt beschikbaar"), volume tiers ("Vanaf 12 flessen …"), composite
*wijnpakketten* linking member products, and offers with expiry text ("Aanbieding
geldig t/m …").

**Bilingual:** NL page furniture and labels (matching the client) around EN dataset
descriptions, plus a small machine-translated NL subset (~30 products) so Dutch
retrieval quality is exercised from day one.

**Deliberately imperfect:** a fixed set of poisoned pages — missing price, malformed
markup, an out-of-stock product, a lapsed offer, a dead link — so the validation
gates (technical plan §4.4) and the freshness rules have something real to catch.
Because generation is seeded and deterministic, the fixture shop doubles as the
**CI target**: crawl → parse → validate → snapshot as an end-to-end test with exact
expected output.

## 5. Real-web integration checks

The fixture shop can't prove networking behavior (caching headers, retries, rate
limiting, encoding surprises). For that, one thin integration suite against sources
with **explicit programmatic-access permission** — an API or open-data license, not
merely "robots.txt doesn't say no":

- **Wikipedia REST API** — fetch + parse a handful of the §3 seed articles.
- **Open Food Facts API** — fetch a few wine products, exercise the messy-data path.

Pre-flight checklist for any future target: documented API or open license → read the
ToS → check robots.txt → custom User-Agent with contact info → ≤1 req/s → honor
ETag/Last-Modified → backoff on errors (technical plan §4.1).

**Explicitly out:** Vivino, Wine-Searcher, and other commercial wine platforms or
shops. ToS-hostile, bot-protected, and unnecessary — everything they'd give us, the
datasets already provide without risk.

## 6. Language strategy

- Schemas are language-agnostic; `Content` chunks (and content-bearing product text)
  carry a `language` field (`en`/`nl`).
- One vector index for both languages via multilingual embeddings — **`bge-m3`**, as
  already selected in the addendum §4 — so a Dutch query can retrieve English chunks
  and vice versa; the LLM answers in the user's language.
- **Golden eval set in both languages:** ~30 EN Q&A pairs grounded in dataset facts
  and ~20 NL pairs grounded in fixture-shop facts (prices, stock, pairings, policy
  pages), run through Phoenix evals (technical plan §5.5) from the first chat MVP.

## 7. Revised near-term milestones

> **Superseded.** The current milestone plan lives in
> [`04-roadmap.md`](./04-roadmap.md); the list below is kept for context.
> (Its Week 1 is done; Week 2 is now roadmap Phase 3.)

This replaces the original "Week 1" of the technical plan §10 and pushes the client
crawl out of the critical path entirely:

1. **Week 1:** repo scaffolding, `schemas` package, X-Wines Slim + WineEnthusiast
   loaders emitting valid snapshots, first published snapshot.
2. **Week 2:** fixture-shop generator + crawler/parser developed against it,
   validation gates, diff reports; Wikipedia/Wikidata knowledge ingest; real-web
   integration checks.
3. **Weeks 3–6:** unchanged (chat MVP → widget → hardening → pilot), all running on
   dataset/fixture snapshots.
4. **Van Bilsen adapter:** written only after consent, as a new ingest source behind
   the same contract — by then the extractors are already proven on a faithful mock
   of its structure.
