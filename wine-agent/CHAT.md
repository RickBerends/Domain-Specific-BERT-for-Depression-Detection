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
bounds, country) → `HybridRetriever` fuses filtered vector and lexical hits
(RRF), relaxing in stages when filters match nothing → `prompt` wraps results
plus session history in an untrusted context block → `LLMClient` streams an
answer → `ChatService` emits typed events → the FastAPI `/chat` endpoint
serializes them as SSE. `SessionStore` keeps a sliding window per session so
follow-ups like "and a cheaper one?" resolve against what was just shown.

## Backends (swap via env, no code change)

| Concern | Default (offline) | Ollama (`WINE_*_BACKEND=ollama`) |
|---|---|---|
| Embeddings | `FakeEmbedder` (hashing) | `bge-m3` (multilingual) |
| LLM | `FakeLLM` (grounded echo) | `qwen2.5:7b-instruct`, streamed |
| Vector index | `InMemoryVectorIndex` | (Chroma adapter drops in here) |

The `FakeLLM` only ever emits text built from retrieved context, so grounding is
testable without a model and CI needs no download.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python -m ingest.xwines                     # 100 X-Wines wines + seed → snapshot
python -m pytest                            # all tests run offline
python -m uvicorn chat.api:app --port 8099  # serve

# then open http://localhost:8099/demo in a browser, or:
curl -N -X POST localhost:8099/chat -H 'Content-Type: application/json' \
     -d '{"message":"red wine under €15?"}'
```

`ingest.xwines` downloads the X-Wines Test CSV (100 wines) from GitHub and
caches it in `data/external/`. The 1k **Slim** version is distributed via
Google Drive only — drop `XWines_Slim_1K_wines.csv` into `data/external/` and
it is used automatically. (`ingest.seed` still publishes the tiny 20-wine
snapshot if you want a minimal catalogue.)

To use a local Ollama instead (on a machine with unrestricted network — model
registries are typically blocked in sandboxed cloud sessions):

```bash
ollama pull qwen2.5:7b-instruct && ollama pull bge-m3   # or qwen2.5:1.5b-instruct on CPU
export WINE_LLM_BACKEND=ollama WINE_EMBED_BACKEND=ollama
python -m ingest.xwines      # re-embed the snapshot with bge-m3
python -m uvicorn chat.api:app --port 8099
```

## API

- `POST /chat` `{message, session_id?}` → SSE: `session` → `cards` → `token`… → `done`
- `GET /demo` → self-contained demo chat page (streaming, cards, EN/NL chips)
- `GET /health` → `{"status":"ok"}`
- `GET /snapshot` → the served catalogue version (`SnapshotRef`)

## Security

See [`SECURITY.md`](./SECURITY.md). In short: the agent is read-only (no tools,
no side effects); untrusted text (customer message, history, retrieved
catalogue) is delimiter-neutralized before prompting (`chat/security.py`); the
SQLite catalogue is opened read-only, parameterized, and never web-served;
configuration is env-only with a committed-secret scan in the test suite; and
the API enforces per-client rate limiting, a CORS allowlist, a body-size cap,
and security headers (`chat/ratelimit.py`, `chat/api.py`).

## Scope and next steps

Built so far: hybrid retrieval with EN/NL metadata filters, staged filter
relaxation, per-session memory (filter carry-over, "cheaper" follow-ups),
policy routing, the demo page, two ingest sources (seed + X-Wines), and the
security controls above (application + edge).
Not yet built: real Phoenix evals (§5.5 — the tracing seam is in place), a
golden eval set, the fixture-shop generator and crawler from
[`context/03-data-acquisition-plan.md`](./context/03-data-acquisition-plan.md),
and the real Preact `embed.js` widget (§6) that replaces the demo page.
