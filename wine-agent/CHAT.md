# Chat subsystem — first vertical slice

A runnable, end-to-end slice of the conversational agent (technical plan §5),
built so it works **offline with deterministic fakes** and switches to a local
Ollama with environment variables only. No client site or dataset needed.

## What's here

```
schemas/          shared contract: Product, Content, ProductCard, SnapshotRef,
                  and the on-disk snapshot format (the ingest↔chat boundary)
ingest/           seed source: hand-written wines → a published snapshot
chat/             the agent: retrieve → generate → stream
data/             seed JSON (tracked); data/snapshot/ is generated (ignored)
tests/            snapshot, retriever, chat service, and API tests
```

The pipeline: `ingest.seed` publishes a snapshot (SQLite catalogue + FTS5 mirror
+ vector index) → `SnapshotReader` reads it → `HybridRetriever` fuses vector and
lexical hits (RRF) → `prompt` wraps them in an untrusted context block →
`LLMClient` streams an answer → `ChatService` emits typed events → the FastAPI
`/chat` endpoint serializes them as SSE.

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

python -m ingest.seed                       # publish the seed snapshot
python -m pytest                            # 19 tests, all offline
python -m uvicorn chat.api:app --port 8099  # serve

curl -N -X POST localhost:8099/chat -H 'Content-Type: application/json' \
     -d '{"message":"which wine pairs well with oysters?"}'
```

To use a local Ollama instead:

```bash
ollama pull qwen2.5:7b-instruct && ollama pull bge-m3
export WINE_LLM_BACKEND=ollama WINE_EMBED_BACKEND=ollama
python -m ingest.seed        # re-embed the snapshot with bge-m3
python -m uvicorn chat.api:app --port 8099
```

## API

- `POST /chat` `{message, session_id?}` → SSE: `session` → `cards` → `token`… → `done`
- `GET /health` → `{"status":"ok"}`
- `GET /snapshot` → the served catalogue version (`SnapshotRef`)

## Scope and next steps

This slice deliberately stops at a working chat spine. Not yet built: query
planning / metadata filters (§5.1 step 3), session memory (§5.3), rate limiting
and CORS (§7), real Phoenix evals (§5.5, the tracing seam is in place), and the
real ingest sources — the dataset loaders and fixture-shop generator from
[`context/03-data-acquisition-plan.md`](./context/03-data-acquisition-plan.md),
which replace `ingest/seed.py` behind the same snapshot contract.
