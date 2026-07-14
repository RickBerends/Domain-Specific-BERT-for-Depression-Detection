# Master Roadmap — everything still to build

This document **supersedes the milestone sections of the earlier plans**
(technical plan §10, data-acquisition plan §7) and combines every outstanding
item from all planning docs, CHAT.md, and SECURITY.md into one prioritized,
exhaustive backlog. The earlier docs remain authoritative for *design detail*;
this one is authoritative for *what's left and in which order*.

Priorities: **P0** = on the critical path to a client pilot · **P1** = needed
before pilot but not blocking the next step · **P2** = needed for production
quality · **P3** = after/with client onboarding · **P4** = nice-to-have.

## 0. Where we stand (done, as of this doc)

- Schemas contract + snapshot format; two ingest sources (hand seed,
  X-Wines Test 100) publishing SQLite+FTS5+vector snapshots.
- Chat service: rule planner (EN/NL routes + color/country/price filters),
  hybrid retrieval (RRF) with staged relaxation and price-ordered "cheaper"
  follow-ups, session memory, streaming SSE API, product-card side-channel.
- `/demo` page with streaming, uniform product-card thumbnails, EN/NL chips.
- Security: read-only agent, delimiter neutralization + injection telemetry,
  env-only secrets + committed-secret scan, read-only non-web-served DB,
  rate limiting, CORS allowlist, body cap, security headers. `SECURITY.md`.
- 83 offline tests. Backends swappable (fake ⇄ Ollama) via env.

---

## Phase 3 — Prove the ingest pipeline (P0)

*The current top priority (was "Week 2"). The chat side is ahead of the ingest
side; this phase builds the actual scraping architecture against a safe target
so the client crawl later is a config change, not new engineering.*

> **Status: DONE (3.1–3.4 core).** `ingest/fixture_shop.py`, `crawler.py`,
> `parsers.py`, `validate.py`, `pipeline.py`, driver `python -m ingest.crawl`.
> Full loop verified: generate → crawl → parse → validate → publish → chat
> serves it. **Deferred to a follow-up:** multi-snapshot versioning dirs +
> rollback pointer (the lifecycle half of 3.4) and hot reload (3.5) — publishing
> currently goes into the single configured snapshot dir via `build_snapshot`.

### 3.1 Fixture-shop generator (P0)
Static-HTML mock wine shop generated from ~200 snapshot rows (X-Wines + seed),
mirroring the van Bilsen URL map (addendum §2) and field quirks (addendum §3):
- Routes: `/product/{id}/{slug}` (+ canonical redirect from `/product/{id}`),
  facet listings (`/rood`, `/wit`, `/frankrijk`, `/chardonnay`, price buckets
  `/tot5euro`…`/vanaf15euro`, `/modelwijnen/{stijl}`), content pages (FAQ,
  bezorging, herroepingsrecht, glossary, pairing guide), HTML `/sitemap`,
  `robots.txt`.
- Quirks reproduced on purpose: Dutch decimal-comma prices ("13,95 per fles"),
  textual stock ("Nog 6 flessen beschikbaar"), volume tiers ("Vanaf 12
  flessen…"), composite *wijnpakketten* linking member products, offers with
  expiry text ("Aanbieding geldig t/m …").
- Poisoned pages for gate testing: missing price, malformed markup, out-of-
  stock, lapsed offer, dead link, duplicate product.
- Deterministic (seeded) so CI can assert exact end-to-end output; servable
  with `python -m http.server` or an in-process test server.
**Accept:** generator emits the site from a snapshot; goldens byte-stable.

### 3.2 Crawler (P0)
`ingest/crawler.py` — polite, resumable fetcher (tech plan §4.2):
- Frontier in SQLite (resume after interrupt), same-domain restriction,
  seed from sitemap else BFS from `/`.
- URL classifier (product / category / content / ignore) with per-class regex
  rules; only classified pages parsed.
- Politeness: robots.txt respect, custom User-Agent with contact, ≤1 req/s
  rate limit, ETag/Last-Modified caching, exponential backoff, max-page cap.
- Raw HTML stored compressed per page keyed by URL+hash → parsers can be
  improved and re-run **without re-crawling** (tech plan §4.4).
**Accept:** full fixture-shop crawl < 2 min; interrupt/resume test passes;
zero requests to off-domain or disallowed paths (asserted).

### 3.3 Parsers / extractors (P0)
`ingest/parsers.py` — per-URL-class extraction to `Product`/`Content`:
- Facet-first metadata (addendum §3): membership on listing pages gives
  color/country/grape/price-bucket/taste-style authoritatively; product pages
  only supply title, price block (incl. sale price + offer expiry), the
  Productinformatie key-value table, prose, stock text, image, breadcrumbs.
- Normalizers: decimal-comma prices → cents; stock text → enum+count;
  volume-tier lines → `PriceTier[]`; vintage from slug fallback; wijnpakket
  member links → package with member product references.
- JSON-LD/OpenGraph path as the preferred branch when present (for future
  sites that have it), CSS/template extractors as fallback.
**Accept:** every fixture product round-trips to a valid `Product`; poisoned
pages produce recorded parse errors, not crashes.

### 3.4 Validation gates + snapshot lifecycle (P0)
`ingest/validate.py` + pipeline driver `python -m ingest.crawl`:
- Gates before publish (tech plan §4.4): product count shrink ≤30% vs prior
  snapshot, schema-validity rate ≥ threshold, price sanity bounds, dead-link
  rate ≤ threshold, offer-expiry sanity.
- Snapshots become directories `data/snapshots/{id}/` with `published` flag;
  chat serves only the latest published one; keep last N; rollback = flip
  the pointer. (Extends `SnapshotRef`; SnapshotReader gains a resolver.)
- Diff report per run (new/changed/removed products) written as JSON+MD;
  failed-publish alert hook (log/webhook stub).
**Accept:** crawl of poisoned fixture fails the gates and does NOT publish;
clean crawl publishes; chat picks up the new snapshot on restart; diff report
matches expectations in CI.

### 3.5 Hot snapshot reload (P1)
Serve a new published snapshot without restarting the chat service (atomic
reader swap on `/snapshot` version change or SIGHUP).
**Accept:** publish while serving → next request answers from new catalogue.

---

## Phase 4 — Data & knowledge breadth (P1)

### 4.1 WineEnthusiast loader (P1)
`ingest/wineenthusiast.py`: load the Kaggle 130k-review CSV (manual download —
Kaggle auth; documented), map to `Product` with real tasting-note prose;
join-by-(variety,country) enrichment mode for X-Wines rows (data-acq §2.3).
License note (CC BY-NC-SA, dev/eval only) in code + docs.
**Accept:** joined snapshot where ≥60% of products carry real prose.

### 4.2 X-Wines Slim/Full support (P1)
Loader already picks up a manually downloaded Slim CSV; add checksum pinning,
column drift detection, and a scale test with 1k (Slim) products; document the
Google-Drive fetch. Full (100k) as a perf exercise only (P4).

### 4.3 Wikipedia/Wikidata knowledge ingest (P1)
`ingest/wikipedia.py`: curated seed list (grape varieties, regions, styles,
viticulture terms — EN + NL pages), fetched via the official REST API with
etiquette headers; chunked ~400 tokens/15% overlap with headings kept;
`language` tag per chunk. `ingest/wikidata.py`: grape⇄region⇄country facts
with multilingual labels → the EN⇄NL terminology bridge used by the planner
(druif → grape). CC BY-SA attribution stored per chunk.
**Accept:** "what is tannin?" / "wat is een tannine?" answered from glossary
chunks; planner maps "syrah uit de rhône" using Wikidata labels.

### 4.4 Real-web integration checks (P1)
Thin network test suite (opt-in, `pytest -m network`): Wikipedia API +
Open Food Facts API fetch/parse/normalize — proving caching headers, retries,
encoding handling against real servers (data-acq §5). Pre-flight checklist
codified as a helper (`ingest/preflight.py`: robots, ToS pointer, UA, rate).
**Accept:** suite green from an unrestricted machine; skipped cleanly in CI.

### 4.5 Content pipeline unification (P2)
One chunking/indexing path for all `Content` regardless of source (seed JSON,
Wikipedia, crawled pages); content chunks join products in the vector index
(currently content is FTS-only) for semantic policy/glossary retrieval.
**Accept:** vector hit on a paraphrased policy question with no keyword
overlap.

---

## Phase 5 — Quality: evals, feedback, observability (P1)

### 5.1 Golden eval set (P1)
`evals/golden.jsonl`: ~30 EN + ~20 NL Q&A pairs grounded in fixture/dataset
facts — price/stock lookups, pairing queries, policy questions, follow-up
chains, refusal cases (out-of-catalogue), injection attempts. Each case:
question, required facts, forbidden content (hallucination markers), route.
### 5.2 Offline eval runner (P1)
`python -m evals.run`: executes the golden set against the composed service
(fake or real LLM), scores retrieval hit-rate, groundedness (answer facts ⊆
context), refusal correctness, route accuracy, filter correctness; JSON + MD
report; CI job runs it with the fake backend as a regression gate.
**Accept:** baseline report committed; CI fails if retrieval hit-rate drops.
### 5.3 Phoenix wiring (P1)
`tracing` extra: OpenInference/OTel exporters live behind the existing span
seam; docker-compose service for Phoenix (internal-only, reverse-proxy auth);
retrieval spans carry query, top-k ids, scores; generation spans carry model,
token counts; `security.injection_suspected` dashboards. Golden-set evals
(groundedness, retrieval relevance) run inside Phoenix against traces.
**Accept:** a traced conversation visible in Phoenix with retrieval + LLM
spans; eval run recorded.
### 5.4 Feedback loop (P1)
`POST /feedback` {message_id, thumbs, comment?} (tech plan §5.6): message ids
added to the SSE `done` event, feedback persisted (SQLite), forwarded to
Phoenix as annotations; widget/demo UI thumbs. Rate-limited like /chat.
**Accept:** thumbs from /demo stored and visible in Phoenix.
### 5.5 Transcript store + retention (P2)
Persist conversations (SQLite) with a retention job (default 30 days) and a
deletion path by session id (GDPR); same policy applied to Phoenix traces.
**Accept:** retention test deletes aged rows; documented in SECURITY.md.

---

## Phase 6 — Real LLM & retrieval upgrades (P1/P2)

### 6.1 Real-model bring-up (P1, needs unrestricted machine)
Ollama `qwen2.5:7b-instruct` (or 1.5b CPU) + `bge-m3`; re-embed snapshot;
run golden evals against the real model (esp. the Dutch cases, addendum §4);
fix OllamaLLM streaming issues found; document tokens/s expectations.
**Accept:** golden-set groundedness ≥ target with real model; Dutch answers in
formal "u" tone verified.
### 6.2 OpenAI-compatible client (P2)
`chat/llm.py` third backend: any OpenAI-compatible endpoint (hosted or
llama.cpp server) via base-url + `require_secret("WINE_LLM_API_KEY")` — the
§5.4 escape hatch made real.
### 6.3 Chroma vector-store adapter (P2)
`chat/vectorstore.py` gains a Chroma-embedded implementation behind the same
interface (plan §3 default); selected by env; migration tool snapshot→Chroma.
In-memory stays the test default.
**Accept:** identical retrieval results on the seed snapshot (parity test).
### 6.4 Planner upgrades (P2)
- Pairing/taste-style vocabulary as first-class filters (map "bij stoofvlees",
  "krachtig & stevig" onto Harmonize/taste facets — addendum §4).
- Grape + vintage filters; "between €10 and €15" ranges; negations ("geen
  mousserend").
- Optional LLM-assisted planning behind the same `plan()` signature (small
  model emits JSON filters; rules remain the fallback) — tech plan §5.1 step 3.
- Language detection stored on the session (reply language stability).
### 6.5 Retrieval quality (P2)
Cross-encoder-free reranking heuristics (facet agreement boost, stock-aware
ordering: in-stock before out), snippet selection for content chunks, dedupe
by producer, and "as of {snapshot date}" freshness note on price/stock answers
(tech plan §5.2 — currently missing).
### 6.6 Concurrency & resilience (P2)
Bounded concurrent generations with a queue + circuit breaker on LLM backend
(tech plan §7 DoS row); request timeouts; SSE heartbeat comments; graceful
client-disconnect cancellation (stop generating when the socket closes).

---

## Phase 7 — Embeddable widget (P2)

### 7.1 `embed.js` web component (P2)
Preact + Vite, single-file bundle ≤25 kB gz, Shadow DOM isolation (tech plan
§6): launcher button → panel (mobile: full-screen sheet, safe-area aware,
≥16px inputs), token streaming, product cards (uniform thumbnails — port the
/demo fix), quick-reply chips, "talk to a human" pinned action, first-message
AI + 18+ disclosure, error/retry states, `sessionStorage` persistence.
### 7.2 Accessibility & theming (P2)
`role="dialog"`, focus trap, Esc close, `aria-live="polite"` stream region,
full keyboard operation, WCAG AA contrast; theming via CSS custom properties
(accent, font) so it inherits shop branding.
### 7.3 Widget auth + CSP (P2)
Public site token (`WINE_SITE_TOKEN` via `require_secret`) checked server-side
per request; CORS allowlist set to shop origin; document the shop-side CSP
snippet; add the shop's image host to the API's CSP `img-src` so real product
photos render (noted in demo.html).
### 7.4 Frontend toolchain & CI (P2)
`frontend/` package: pnpm, Vite build, vitest + Playwright smoke (embed on a
blank page, stream a fake answer), bundle-size budget check in CI.
**Accept:** `<script src=".../embed.js">` on a plain HTML page gives the full
chat experience against a running API.

---

## Phase 8 — Ops, deployment, CI/CD (P2)

### 8.1 Docker Compose stack (P2)
Services per tech plan §9: `chat-api`, `ollama`, `phoenix`, `caddy` (TLS,
reverse proxy, second rate-limit tier), `scraper` (cron one-shot), optional
`qdrant`; internal-only network for everything except caddy; named volumes;
non-root containers; healthchecks. `WINE_TRUST_PROXY=1` behind caddy.
### 8.2 CI pipeline (P2)
GitHub Actions: lint (ruff) + typecheck (mypy, tighten annotations) + tests +
offline eval gate + `pip-audit` + secret-scan test + frontend build/budget.
Pinned dependencies (`uv lock` or pip-tools).
### 8.3 Scheduling & freshness (P2)
Cron/APScheduler: nightly full crawl + optional hourly light pass over product
URLs (lastmod/content-hash driven — tech plan §4.6); publish gate alerts (mail/
webhook); manual re-crawl trigger endpoint (admin-only) for the 1st-of-month
churn (addendum §5).
### 8.4 Backups & monitoring (P2)
Nightly DB/snapshot backups with restore test; uptime + latency probes;
log hygiene (no message bodies at INFO); disk-space watchdog for raw-HTML
store; load test (k6 or locust) against /chat with the fake backend.

---

## Phase 9 — Client onboarding: van Bilsen (P3, consent-gated)

1. **Consent + agreements**: written permission to crawl and to embed; agree
   staleness budget (e.g. 24h descriptions / 1h stock), retention policy,
   contact for the bot UA string. *(Blocks everything below.)*
2. **Adapter**: URL-class rules + extractors for the live site (validated
   against the fixture shop first — they mirror it by design); NL-first
   content ingest (wijnspijswijzer, begrippenlijst, proeverijen with event
   dates filtered by snapshot date); exclude horeca/login areas.
3. **Shadow period**: nightly crawls + gates for ≥1 week, diff reports
   reviewed with the shop; no chat exposure.
4. **Staging embed**: widget on a staging copy (tech plan §10 week 4), golden
   set extended with 20 real-catalogue NL cases; NIX18 wording review.
5. **Pilot**: feature-flagged on the live site, feedback loop via §5.4,
   weekly retrieval/prompt iteration; success criteria (deflection rate,
   thumbs-up ratio, zero hallucinated prices) agreed up front.

---

## Cross-cutting (any phase)

- **C1. Repo extraction (P1, pending access):** the original request was a
  standalone `wine-agent` repository; creation was blocked by GitHub-app
  permissions. Once granted (claude.ai admin settings), extract `wine-agent/`
  with history (`git subtree split`) into `RickBerends/wine-agent`, set up CI
  there, and leave a pointer in this repo.
- **C2. Session persistence (P2):** `SessionStore` behind SQLite (or Redis in
  compose) so restarts keep conversations; TTL unchanged.
- **C3. Type/lint baseline (P2):** ruff + mypy strict on `schemas/`, gradual
  elsewhere; pre-commit config.
- **C4. Docs (P2):** architecture diagram refresh, ADRs for the deviations
  already made (in-memory vector store, Test-vs-Slim dataset, flag-don't-block
  injection policy), CONTRIBUTING, runbook (deploy, rollback, re-crawl).
- **C5. Compliance (P2/P3):** GDPR checklist (retention §5.5, deletion path,
  privacy note text for the widget), NIX18 wording sign-off by the shop,
  dataset licenses honored on any redistribution (X-Wines citation shipped,
  WineEnthusiast NC-only).
- **C6. Performance (P4):** Slim/Full-scale retrieval benchmarks, embedding
  batch pipeline, FTS5 tokenizer tuning for Dutch compounds, HTTP/2 for SSE.

## Suggested execution order

1. **Phase 3** (3.1 → 3.4, then 3.5) — the critical path. Everything else
   layers on a proven pipeline.
2. **5.1–5.2** (golden set + offline runner) immediately after — cheap, locks
   quality in CI before more moving parts arrive.
3. **Phase 4** (4.1–4.4) and **C1** in parallel where convenient.
4. **6.1** on real hardware as soon as available (independent of the rest);
   then 5.3–5.4, 6.3–6.6.
5. **Phase 7** widget, then **Phase 8** ops hardening around it.
6. **Phase 9** the moment consent exists — by then it's an adapter, not a
   project.
