# Wineshop Conversational Agent

A grounded, embeddable chat assistant for a wine merchant's existing website.
Reference target: [Wijnkoperij van Bilsen](https://www.wijnkoperijvanbilsen.nl/) (Tilburg, NL).

Two **fully separable** subsystems:

| Subsystem | Runs | Responsibility |
|---|---|---|
| `ingest/` | offline, scheduled | crawl → parse → validate → publish snapshot (catalog + vector index) |
| `chat/`   | online, serving    | retrieve → generate → stream to the embedded widget |

They share nothing but the `schemas/` contract package and a published-snapshot tag in the data layer.

## Stack

Python 3.12 · FastAPI · Ollama (local LLM + embeddings) · Chroma/Qdrant · SQLite FTS5 ·
Arize Phoenix (tracing + evals) · Preact web component shipped as a single `embed.js`

## Planning docs

See [`context/`](./context):

- [`01-technical-plan.md`](./context/01-technical-plan.md) — architecture, scraping pipeline, chat service, frontend, security, milestones
- [`02-vanbilsen-site-addendum.md`](./context/02-vanbilsen-site-addendum.md) — findings from the live site and how they change the plan
- [`03-data-acquisition-plan.md`](./context/03-data-acquisition-plan.md) — datasets, open knowledge sources, and safe scrape targets for building without the client site
- [`04-roadmap.md`](./context/04-roadmap.md) — **master roadmap**: the exhaustive prioritized backlog of everything still to build (supersedes earlier milestone lists)

## Status

Runnable end-to-end: X-Wines catalogue ingest, hybrid retrieval with EN/NL
metadata filters ("red under €15" / "rode wijn tot 15 euro"), session memory
with follow-ups, and a browser demo at `/demo` — offline by default with
deterministic fakes, Ollama-ready via env vars. See [`CHAT.md`](./CHAT.md) to
run it. Remaining work follows the milestones in the technical plan.

## Note on permission

The target site belongs to a real business. Do not crawl or deploy against it without their explicit consent.
