# Chat subsystem

The wine chat assistant. It answers questions about the shop's wines using only
the shop's own data — it finds the right wines, then writes a short, friendly reply.

There are two ways to run it:

- **Offline (default):** canned replies, no setup, no internet. Great for
  development and tests.
- **Groq:** real AI-written answers from a free cloud model. Great for a live demo.

## What's here

```
schemas/   shared data types (Product, Content, ProductCard, SnapshotRef)
ingest/    builds the catalogue: a hand-written seed + the X-Wines dataset
chat/      the assistant: find wines → write reply → stream to the page
data/      seed files (kept in git); the built catalogue is generated
tests/     the test suite
```

## How it works

When you send a message, the assistant works out what you want — a colour, a
price limit, a country, or a grape — and finds matching wines in the catalogue.
It then picks three to show: a **best match**, a **best value**, and **something
different**, and writes a short reply about them. It understands English and
Dutch and answers in the language you used. It also remembers the last few
messages, so a follow-up like "and a cheaper one?" works.

If you ask something vague ("I need a wine for a dinner"), it asks one quick
question first instead of guessing.

## Run it (offline)

```powershell
cd wine-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate

pip install -e ".[dev]"

python -m ingest.xwines             # build the catalogue (100 wines) into data/snapshot/
python -m pytest                    # run the tests (all offline, no internet)
python -m uvicorn chat.api:app --port 8099
```

Now open **http://localhost:8099/demo** and start chatting. Try an English and a
Dutch question in the same chat.

Already set up before? The catalogue is saved on disk, so you can just start the
server again:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn chat.api:app --port 8099
```

> Port 8099 busy? Use a different one, e.g. `--port 8100`.

Quick check without a browser:

```powershell
curl.exe -s http://localhost:8099/snapshot
curl.exe -N -X POST http://localhost:8099/chat -H "Content-Type: application/json" -d '{"message":"red wine under 15 euro?"}'
```

## Use Groq for real answers

The offline mode gives canned replies. For real, AI-written answers, use Groq —
it's free, cloud-hosted, and needs no special hardware.

```powershell
pip install -e ".[groq]"
# get a free key at console.groq.com
$env:GROQ_API_KEY = "gsk_..."; $env:WINE_LLM_BACKEND = "groq"
python -m uvicorn chat.api:app --port 8099
```

That's it — the assistant now writes real replies and matches your language
(English or Dutch) automatically. The default model is `llama-3.1-8b-instant`,
which is plenty for short, grounded answers. Set `WINE_GROQ_MODEL` to change it.

Good to know: the free tier is generous for a small demo, and Groq does not
train on your prompts. If Groq is unreachable, the chat shows a clean error
instead of hanging.

## Backends

Chosen entirely by environment variables — no code changes.

| | Offline (default) | Groq |
|---|---|---|
| Replies | `FakeLLM` — canned, English only | real model, matches your language |
| Setup | none | free API key |
| Internet | not needed | needed |

The offline `FakeLLM` never translates. If you write in Dutch, it prefixes its
reply with `[fake-backend, English-only]` so it's obvious you're still on the
stub and not on a real model. The active backend also shows in the demo page
header and in `GET /snapshot`.

## API

- `POST /chat` `{message, session_id?}` → a stream of events: `session` →
  `cards` → `token`… → `done` (or `error` if something goes wrong)
- `GET /demo` → the demo chat page
- `GET /health` → `{"status":"ok"}`
- `GET /snapshot` → the catalogue version and which backend is live

### Cards and images

Each recommendation is a card with real catalogue facts only — name, price,
grapes, country, vintage, stock. No star ratings, reviews, or awards are ever
shown, because that data doesn't exist and inventing it would mislead shoppers.

Most wines have no photo, so cards use one of three sample bottle images in
`wine-agent/img/` for the three recommendation slots. If those files aren't
present, cards fall back to a simple colour-coded placeholder. Every card image
is cropped to the same fixed size, so cards always line up neatly.

## Security

See [`SECURITY.md`](./SECURITY.md). In short: the assistant is read-only (it
can't take actions); customer text and catalogue text are cleaned before being
put in the prompt; the database is read-only and never served on the web;
secrets live only in environment variables; and the API has rate limiting, a
CORS allowlist, a request-size cap, and security headers.

## What's done and what's next

**Done:** English/Dutch understanding and replies, price/colour/country/grape
filters, three curated recommendations per answer, session memory, a friendly
tone, the offline and Groq backends, the demo page, the security controls
above, and the ingest pipeline (see [`context/04-roadmap.md`](./context/04-roadmap.md)).

**Next:** evaluation tests for answer quality, and the real embeddable widget
that replaces the demo page. The full backlog is in
[`context/04-roadmap.md`](./context/04-roadmap.md).
