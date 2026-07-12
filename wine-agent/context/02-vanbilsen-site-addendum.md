# Site Addendum — wijnkoperijvanbilsen.nl

Findings from inspecting the live site (homepage + a product page, July 2026), and how they change the generic plan.

## 1. What the site is

Wijnkoperij van Bilsen, a wine merchant in Tilburg (NL). Dutch-language webshop, roughly **1,700–1,900 products** (facet counts: rood 780, wit 862, rosé 74, plus champagne, mousserend, alcoholvrij, port, dessertwijn, wijnpakketten, glassware). Ships to NL/BE/DE/FR, free delivery from €75, prices incl. BTW.

**Good news for the pipeline:** the site is fully **server-rendered** — a plain HTTP fetch returns complete content. No Playwright needed for anything seen so far. Image paths (`/product-images/.../conversions/...`) strongly suggest a custom **Laravel** build (Spatie MediaLibrary pattern) with a CSRF meta token; no JSON-LD product markup was observed, so plan on **CSS/template extractors** rather than structured-data parsing.

> **Permission first.** This is a real, family-run business. Before crawling or shipping anything on their domain, get their explicit OK (ideally they're the client). The polite-crawl rules from §4.1 of the main plan apply in full.

## 2. URL map (observed)

| Type | Pattern | Examples |
|---|---|---|
| Product | `/product/{id}` → canonical `/product/{id}/{slug}` | `/product/1148970111/prima-luna-frascati-2025` |
| Color category | `/{kleur}` | `/rood`, `/wit`, `/rose`, `/champagne`, `/mousserende-wijn`, `/alcoholvrij`, `/port`, `/dessertwijn` |
| Country | `/{land}` | `/frankrijk`, `/italie`, `/spanje`, `/zuid-afrika` |
| Grape | `/{druif}` | `/chardonnay`, `/pinot-noir`, `/syrah` |
| Price bucket | `/tot5euro`, `/van5tot8euro`, `/van8tot15euro`, `/vanaf15euro` | |
| Taste style | `/modelwijnen/{stijl}` | `krachtig-stevig`, `vol-verfijnd`, `zacht-rijk` |
| Faceted search | `/zoeken/{facet}/{waarde}` | `/zoeken/kleur/rood`, `/zoeken/dranksoort/wijnpakket` |
| Content | assorted slugs | `/klantenservice/veelgestelde-vragen`, `/bezorging`, `/wijnspijswijzer`, `/begrippenlijst`, `/proeverijen`, `/overzicht-producenten`, `/herroepingsrecht`, `/geborgde-werkwijze-leeftijdscheck-18` |
| Sitemap | `/sitemap` (HTML) | also try `robots.txt` / `sitemap.xml` at runtime |

Vintage frequently appears in the product slug (`...-2025`), which is a useful extraction fallback.

## 3. Scraping strategy, tailored

**Facet-first crawling (the big shortcut).** Instead of extracting color/country/grape/price/taste per product page, crawl the facet listing pages and record *membership*: every product ID found under `/rood` is red, under `/frankrijk` is French, under `/modelwijnen/vol-verfijnd` has that taste style. This yields clean, authoritative metadata from ~250 listing pages, immune to prose-parsing errors. Product IDs are deduped via the canonical `/product/{id}` key.

**Product-page extraction** then only needs: title (h1), price block — regular price, sale price, and offer expiry text ("Aanbieding geldig t/m 31 juli"), the **Productinformatie** key–value table, description prose, stock text, image URL, and breadcrumbs. Notable field quirks:

- **Prices use Dutch decimal commas** ("139,10") and units like "per fles" / "per wijnpakket" — normalize to cents + unit enum.
- **Volume tiers** exist ("Vanaf 12 flessen 10,95 per fles") and packaging variants ("Fles" / "Doos (6)") — model as a price-tier list.
- **Stock is textual**: "Beperkt beschikbaar", "Nog 6 flessen beschikbaar" — map to an enum {in_stock, limited, n_left, out} + optional count.
- **Tasting notes and food pairing live in one prose paragraph** (e.g., aromas, palate, then "Lekker bij…"). Keep the paragraph intact for retrieval; optionally run a one-time LLM pass at index time to split `tasting_notes` / `food_pairing` fields — but that's polish, not required.
- **Wine packages** (`wijnpakketten`) are composite products listing their member wines with links — index the package with references to member product IDs so the agent can answer "wat zit er in het julipakket?".
- **Offers expire** — store `offer_valid_until`; the chat layer must not quote a lapsed sale price (filter at retrieval by snapshot date).

**Content pages to index** (high answer-value): FAQ, bezorging (delivery), herroepingsrecht (returns/withdrawal), veilig winkelen, the NIX18 leeftijdscheck page, over-ons pages, **wijnspijswijzer** (food-pairing guide — pairs beautifully with catalog retrieval), **begrippenlijst** (wine glossary — lets the bot explain jargon), producenten, and the **proeverijen agenda** (time-sensitive: include event dates in metadata so past events are filtered out). Opening hours and contact details sit in the footer — parse once into a `shop_info` record rather than retrieving them semantically.

**Scale check:** ~1,900 products + ~250 facet pages + ~80 content pages ≈ 2,250 fetches → at 1 req/s a full crawl is ~40 minutes. Nightly full crawl is comfortably feasible; no incremental machinery needed at first.

## 4. Chat-layer adjustments

- **Language: Dutch, formal "u"** — mirroring the site's own tone. Default to Dutch; answer in the user's language if they switch. Add "handles Dutch well" to model selection criteria: `qwen2.5:7b-instruct` is decent in Dutch, but build the **golden eval set in Dutch** and verify; if quality disappoints, `gemma2/gemma3` variants or a bigger quant are the fallback — config change only.
- **Embeddings:** prefer a multilingual model — **`bge-m3` via Ollama** — over `nomic-embed-text` for Dutch queries against Dutch text.
- **Taste styles as a first-class filter.** The site's own "smaakstijl" taxonomy (Krachtig & stevig, Vol & verfijnd, Zacht & rijk, …) maps directly onto how customers ask ("een volle rode wijn bij stoofvlees") — route such queries to metadata filters before vector search.
- **Quick-reply chips in Dutch**, grounded in real facets: "Rode wijn tot €15", "Wijn bij vis", "Wat is er deze maand in de aanbieding?", "Openingstijden".
- **Compliance, NL-specific:** the shop operates under **NIX18** (no alcohol under 18) with a documented age-check procedure. The widget's first-message disclosure should state (in Dutch) that offers are for 18+, and the system prompt inherits the no-minors/no-excess rules from the main plan. Prices quoted as incl. BTW, delivery free from €75, shipping NL/BE/DE/FR — all answerable from the `shop_info` record.
- **Product cards** link to the canonical slug URL so customers land on the real product page for purchase (keeps NIX18 checkout and payment flows entirely on the shop's side).

## 5. Revised risk notes

- No JSON-LD found → selector breakage is the main fragility. Mitigation: the snapshot validation gates (§4.4 of main plan) plus facet-first metadata, which is much more stable than per-page parsing.
- CSRF token in meta is for forms, not reads — GET crawling is unaffected.
- Monthly churn is real (maandaanbiedingen, wijnbericht packages rotate) — nightly crawls handle it; consider a same-day manual re-crawl trigger for the 1st of the month.
- The horeca (trade) section may sit behind login — **exclude it**; the agent serves retail customers only.
