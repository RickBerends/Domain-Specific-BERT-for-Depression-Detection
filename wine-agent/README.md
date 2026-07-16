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

Python · FastAPI · SQLite FTS5 + vector search · Groq (free cloud LLM) with a
built-in offline fallback · Preact web component shipped as a single `embed.js`

## Planning docs

See [`context/`](./context):

- [`01-technical-plan.md`](./context/01-technical-plan.md) — architecture, scraping pipeline, chat service, frontend, security, milestones
- [`02-vanbilsen-site-addendum.md`](./context/02-vanbilsen-site-addendum.md) — findings from the live site and how they change the plan
- [`03-data-acquisition-plan.md`](./context/03-data-acquisition-plan.md) — datasets, open knowledge sources, and safe scrape targets for building without the client site
- [`04-roadmap.md`](./context/04-roadmap.md) — **master roadmap**: the exhaustive prioritized backlog of everything still to build (supersedes earlier milestone lists)

## Status

Runnable end-to-end: catalogue ingest, English/Dutch search and filters
("red under €15" / "rode wijn tot 15 euro"), session memory with follow-ups,
and a browser demo at `/demo`. It runs **offline by default** (canned replies,
no setup) and switches to **Groq** for real AI answers with one API key. See
[`CHAT.md`](./CHAT.md) to run it, and [`context/04-roadmap.md`](./context/04-roadmap.md)
for what's next.

## Note on permission

The target site belongs to a real business. Do not crawl or deploy against it without their explicit consent.
