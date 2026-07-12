# Chat subsystem — runnable slice

A runnable, end-to-end slice of the conversational agent (technical plan §5),
built so it works **offline with deterministic fakes** and switches to a local
Ollama with environment variables only. No client site needed.

## What's here

```
schemas/          shared contract: Product, Content, ProductCard, SnapshotRef,
                  and the on-disk snapshot format (the ingest↔chat boundary)
ingest/           snapshot sources: hand-written seed + X-Wines dataset loader
chat/             the agent: plan → retrieve → generate → stream (+ /demo page)
data/             seed JSON (tracked); snapshot/ and external/ are generated
tests/            snapshot, retriever, planner, filters, sessions, API tests
```

The pipeline: an ingest source (`ingest.seed` or `ingest.xwines`) publishes a
snapshot (SQLite catalogue + FTS5 mirror + vector index) → `SnapshotReader`
reads it → `planner` extracts route + metadata filters (EN/NL: color, price
bounds, country, grape-varietal names, negation-aware — "not red, white
please" no longer locks onto "red") → `HybridRetriever` fuses filtered vector
and lexical hits (RRF), relaxing in stages when filters match nothing →
`lang.detect_language` guesses Dutch vs. English from the message →
`select_recommendations` curates up to `card_limit` (default 3) complementary
picks — best match, best value, something different — from the ranked pool,
each with a data-driven `reason`, rather than dumping the raw top-N → `prompt`
wraps the curated results plus session history in an untrusted context block,
with a per-turn language instruction and an explicit self-check rule (verify
each recommendation against the customer's stated hard constraints before
presenting it) → `LLMClient` streams a warm, sommelier-toned answer →
`ChatService` emits typed events → the FastAPI `/chat` endpoint serializes
them as SSE. `SessionStore` keeps a sliding window per session so follow-ups
like "and a cheaper one?" resolve against what was just shown. If the LLM
backend fails or times out mid-stream, the service catches it and emits a
clean, localized `error` event instead of dying silently.

## Backends (swap via env, no code change)

| Concern | Default (offline) | Ollama (`WINE_LLM_BACKEND=ollama`) | Groq (`WINE_LLM_BACKEND=groq`) |
|---|---|---|---|
| Embeddings | `FakeEmbedder` (hashing) | `bge-m3` (multilingual) | — (LLM only; embeddings stay `fake`/`ollama`) |
| LLM | `FakeLLM` (grounded echo) | `qwen2.5:7b-instruct`, streamed | `llama-3.1-8b-instant`, streamed |
| Vector index | `InMemoryVectorIndex` | (Chroma adapter drops in here) | (unaffected — LLM-only backend) |

The `FakeLLM` only ever emits text built from retrieved context, so grounding is
testable without a model and CI needs no download. `FakeLLM` is deliberately
**English-only** — it does not translate. If the incoming message is detected
as Dutch (`chat.lang.detect_language`), it prefixes its reply with
`[fake-backend, English-only]` instead of silently answering in the wrong
language, so it's obvious when a real Ollama backend (which does honor the
detected language, reinforced per turn — see `chat/prompt.py`) hasn't been
wired up yet. The active backend is visible in `GET /snapshot`'s `backend`
field and in the demo page's header subtitle.

## Run it

**First time / fresh checkout:**

```bash
cd wine-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell. Use `source .venv/bin/activate` on macOS/Linux.

pip install -e ".[dev]"

python -m ingest.xwines                     # 100 X-Wines wines + seed → publishes data/snapshot/
python -m pytest                            # 54 tests, all offline (fake backends, no network)
python -m uvicorn chat.api:app --port 8099  # serve
```

Then open **http://localhost:8099/demo** in a browser and start chatting —
try both an English and a Dutch question in the same session. The header
subtitle shows the wine count and which LLM backend is live (`fake` or
`ollama`).

**Already set up? Just re-run the server** (a snapshot already exists under
`data/snapshot/` once `ingest.xwines`/`ingest.seed` has been run once):

```bash
.\.venv\Scripts\Activate.ps1
python -m uvicorn chat.api:app --port 8099
```

> If port 8099 is already in use (e.g. a server left running from an earlier
> session), either stop that process or pick a different port with
> `--port 8100` — the client-side demo page and API don't hardcode the port.

**Sanity-check without a browser:**

```bash
curl.exe -s http://localhost:8099/snapshot
# {"snapshot_id":"...","product_count":120,...,"backend":"fake"}

curl.exe -N -X POST http://localhost:8099/chat `
    -H "Content-Type: application/json" `
    -d '{"message":"red wine under €15?"}'
```

`ingest.xwines` downloads the X-Wines Test CSV (100 wines) from GitHub and
caches it in `data/external/`. The 1k **Slim** version is distributed via
Google Drive only — drop `XWines_Slim_1K_wines.csv` into `data/external/` and
it is used automatically. (`ingest.seed` still publishes the tiny 20-wine
snapshot if you want a minimal catalogue.)

**To use a local Ollama instead** (on a machine with unrestricted network —
model registries are typically blocked in sandboxed cloud sessions):

```bash
ollama pull qwen2.5:7b-instruct && ollama pull bge-m3   # or qwen2.5:1.5b-instruct on CPU
$env:WINE_LLM_BACKEND = "ollama"; $env:WINE_EMBED_BACKEND = "ollama"   # PowerShell
python -m ingest.xwines      # re-embed the snapshot with bge-m3
python -m uvicorn chat.api:app --port 8099
```

With `WINE_LLM_BACKEND=ollama`, the assistant replies in whichever language
(Dutch or English) the customer's message was detected as, reinforced per
turn — no separate flag needed. If Ollama isn't running or the model isn't
pulled, requests fail fast (~5s connect timeout) with a clean, localized
`error` event in the chat instead of hanging.

**On a vague opening message** ("I need a wine for my anniversary dinner" —
no colour/price/country and no specific wine name, `chat.planner
.is_vague_request`), the assistant asks one short clarifying question
instead of guessing — no cards yet. Once you answer, the normal recommend
flow runs, and the reply calls out one of the three curated cards as its
personal top pick. `FakeLLM` can't generate a genuine clarifying question
(it only echoes context), so it uses a canned bilingual one instead — same
"honest about being a stub" approach as the language tag.

**A note on CPU-only Ollama speed**: `qwen2.5:7b-instruct` on CPU can be slow
— the full recommend prompt (system prompt + 3 annotated products + history)
is meaningfully larger than the short clarifying-question prompt, and on
constrained/shared hardware it can take several minutes rather than seconds.
`WINE_OLLAMA_READ_TIMEOUT` (default 180s, `chat/llm.py`) governs how long a
single streamed response is allowed to take before the chat gets a clean
error instead of hanging — raise it if you have the hardware and patience,
or switch to `qwen2.5:1.5b-instruct` for much faster (if less capable)
responses on modest CPUs. **If the machine has no GPU, Groq (below) is the
better fix** — cloud-hosted, free, and fast regardless of local hardware.

**To use Groq instead** (free, cloud-hosted, no local hardware needed — the
fix for CPU-only Ollama being too slow for interactive chat):

```bash
pip install -e ".[groq]"                         # adds the openai client
# get a free key at console.groq.com
$env:GROQ_API_KEY = "gsk_..."; $env:WINE_LLM_BACKEND = "groq"   # PowerShell
python -m uvicorn chat.api:app --port 8099
```

Groq's API is OpenAI-compatible, so `GroqLLM` (`chat/llm.py`) is just the
official `openai` client pointed at Groq's `base_url` — no bespoke HTTP
client, unlike `OllamaLLM`. Default model is `llama-3.1-8b-instant`
(`WINE_GROQ_MODEL` to change) — an 8B model is already enough for this
task's short, grounded generations (per the model-size analysis in
`context/04-feedback-remediation-plans.md`), and the 8B tier has a much
higher free-tier daily request allowance than `llama-3.3-70b-versatile`
(the upgrade option if reply quality needs to be more consistently strong).
Free-tier limits to be aware of: the 70B model is capped at 30 requests/min,
1,000/day, 12K tokens/min — comfortably enough for interactive use at
low-to-moderate traffic, but worth watching if volume grows. Unlike
Google's Gemini free tier, Groq does not train on free-tier prompts — a
real consideration for a customer-facing chat.

## API

- `POST /chat` `{message, session_id?}` → SSE: `session` (carries `backend`) →
  `cards` → `token`… → `done` (or an `error` event if input is invalid or the
  LLM backend fails)
- `GET /demo` → self-contained demo chat page (streaming, curated 3-card
  grid, typing indicator, example-prompt chips)
- `GET /health` → `{"status":"ok"}`
- `GET /snapshot` → the served catalogue version (`SnapshotRef`) plus the
  active `backend` (`"fake"`, `"ollama"`, or `"groq"`)

### Product cards

Each card in the `cards` event (`schemas.ProductCard`) carries, beyond
`slug`/`name`/`price_eur`/`url`: `grape_varieties`, `country`, `vintage`,
`stock_status` (all real catalogue fields, not fabricated), `role`
(`"best_match" | "best_value" | "different" | null`) with a short data-driven
`reason`, and `closest_alternative` (true when the retriever had to relax the
customer's filters to find anything, so the UI can show an honest badge
instead of presenting a mismatch as a perfect fit).

Deliberately **not** included anywhere: star ratings, review counts, awards,
or "N% of customers also liked..." social-proof stats — there is no review,
purchase, or awards data anywhere in the catalogue, and fabricating those
numbers would mislead shoppers.

**Images**: most of the catalogue (all 100 X-Wines-sourced products — that
dataset has no image column) has no real photo, and the 20 seed products that
do point at a non-resolving fixture domain. Three real bottle photos live in
`wine-agent/img/` (`img1.png`/`img2.png`/`img3.png`, served via a static
mount at `/img`) and are used as card art for the three curated
recommendation slots — `best_match` → `img1.png`, `best_value` → `img2.png`,
`different` → `img3.png` (`chat/images.py`'s `ROLE_SAMPLE_IMAGES`). These are
real photos of specific bottles, not the actually-recommended product, so
they're deliberately only used as slot art, never claimed to depict that
exact wine. If a product has its own real `image_url`, that's used instead;
if `/img` isn't present at all (e.g. a fresh checkout without those files),
cards fall back to a deterministic, colour-coded generated placeholder (via
`placehold.co`, `WINE_PLACEHOLDER_IMAGE_BASE_URL` to point it elsewhere).

## Scope and next steps

Built so far: hybrid retrieval with EN/NL metadata filters (including
negation handling and grape-varietal vocabulary), staged filter relaxation
with an honest "closest alternative" signal surfaced end-to-end (retriever →
prompt → both LLM backends → card badge), curated 3-card recommendations
(best match / best value / something different) instead of a raw scrolling
list, a warm sommelier-toned system prompt, per-session memory (filter
carry-over, "cheaper" follow-ups), policy routing, per-turn EN/NL language
detection and matching, three swappable LLM backends (`fake`/`ollama`/`groq`)
with resilient, bounded-timeout error handling on both real backends, a
redesigned demo page (typing indicator, safe markdown rendering, accessible
markup, responsive layout), and two ingest sources (seed + X-Wines).

Not yet built (see `context/04-feedback-remediation-plans.md` and the latest
plan for the full write-ups): rate limiting, CORS, and stricter session-id
validation, prompt-injection delimiter sanitization, real Phoenix evals
(§5.5 — the tracing seam is in place), a golden eval set, the fixture-shop
generator and crawler from
[`context/03-data-acquisition-plan.md`](./context/03-data-acquisition-plan.md),
the real Preact `embed.js` widget (§6) that replaces the demo page, and the
larger next-level features (guided occasion/style Q&A, a running session
taste profile, a persistent "wine drawer" of saved/recently-viewed wines) —
scoped as session-only, sequenced last, and not yet started.
