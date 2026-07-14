# Remediation plans: security, UI, Ollama/language (from feedback.md)

> Written from a full-codebase audit in response to `wine-agent/feedback.md`.
> Line numbers below reflect the code at audit time — some have since moved.
> **Status:** Plan 2 (Chat UI Redesign) and Plan 3 (Ollama Reliability + Language
> Matching) are **implemented** (see `chat/lang.py`, `chat/llm.py`,
> `chat/prompt.py`, `chat/service.py`, `chat/api.py`, `chat/static/demo.html`,
> and `CHAT.md`). Plan 1 (Security Hardening) is **not yet implemented** — it
> was deliberately deferred so the initial pass could focus on making the live
> chat itself work well; it remains here as the next piece of work.

## Context

`wine-agent/feedback.md` raises three separate concerns about the chat subsystem: (1) security hardening against injection/secret-exposure/raw-data-dump risks, (2) the visual quality of the `/demo` chat UI, and (3) the Ollama backend not "feeling native" plus replies not matching the user's input language (Dutch vs English). This plan addresses each independently — they touch overlapping files (`chat/llm.py`, `chat/service.py`, `chat/prompt.py`, `demo.html`) but are scoped as three separate change sets so they can be implemented and reviewed one at a time. Findings below come from a full read of `chat/api.py`, `chat/config.py`, `chat/prompt.py`, `chat/llm.py`, `chat/service.py`, `chat/sessions.py`, `chat/planner.py`, `chat/snapshot.py`, `chat/static/demo.html`, and `schemas/models.py`.

---

## Plan 1: Security Hardening — NOT YET IMPLEMENTED

**Why:** SQL injection and raw-catalogue-dump vectors are already closed today (parameterized queries throughout `chat/snapshot.py`; FTS5 tokens sanitized in `_to_fts_query()` lines 149-161; `ProductCard` already restricts exposed fields to `slug/name/price_eur/currency/image_url/url`, `schemas/models.py:118-140`; retrieval is always `top_k`-bounded server-side, `retriever.py`, never client-controlled). The real gaps: no CORS policy, no rate limiting, a client-controlled `session_id`, internal filesystem paths leaking into error responses, unhandled mid-stream Ollama failures, and a prompt-injection defense that is text-only (one sentence in the system prompt) with no structural enforcement. This is a small demo-stage app — no WAF, no secrets manager, no external auth service; fixes should be proportionate.

**Changes:**

1. **CORS allowlist** — In `chat/api.py` where `FastAPI()` is constructed, add `CORSMiddleware` with origins from a new `WINE_CORS_ORIGINS` env var in `chat/config.py` (comma-separated, default empty = same-origin only, never `*`).

2. **Rate limiting** — Add a small in-process token-bucket keyed by client IP, styled like `SessionStore`'s existing TTL-eviction pattern (`chat/sessions.py:33-62`). New `chat/ratelimit.py` (or a class alongside `SessionStore`), wired into `POST /chat` in `chat/api.py`. Cap ~20 req/min/IP, return `429` with a generic body. No new heavy dependency needed for this scale.

3. **Server-owned `session_id`** — `chat/api.py:33` (request model) and `chat/api.py:69` (`req.session_id or str(uuid.uuid4())`) currently trust any client-supplied string as a dict key into `SessionStore`. Validate: accept a client-supplied `session_id` only if it matches a strict UUID regex **and** already exists in `SessionStore`; otherwise mint a fresh server-side UUID. Prevents unbounded session creation and session-id guessing/collision.

4. **Global exception handler + path-leak fix** — Add `@app.exception_handler(Exception)` in `chat/api.py` returning a generic `{"detail": "internal error"}` with no exception text; log the real exception server-side via `logging`. *(Partially done: the specific `FileNotFoundError` path-leak at `GET /snapshot` and `POST /chat` was already fixed as a quick win alongside Plan 3 — see `chat/api.py`. The general `@app.exception_handler(Exception)` catch-all for arbitrary unhandled exceptions is still outstanding.)*

5. **Ollama stream error handling** — *(Done — implemented alongside Plan 3 item 4: `chat/llm.py`'s `OllamaLLM.stream` now has a bounded timeout and raises `LLMError`, caught in `chat/service.py` to emit a clean `{"type": "error", ...}` event.)*

6. **Harden prompt-injection defense** — `chat/prompt.py`, `build_user_message()` (lines 59-92) concatenates history, the raw user question (line 87), and retrieved context (lines 88-91) using plain-text delimiters (`[QUESTION]`, `[CONTEXT]`, `[HISTORY]`) with no escaping — a user can inject literal `[/QUESTION]\n[CONTEXT]\n...` text to forge fake instruction blocks. Add a `_sanitize_delimiters(text: str) -> str` helper that strips/escapes these tokens (case-insensitive) from both user input and retrieved snippets before interpolation. Keep the existing system-prompt sentence as defense-in-depth; the structural fix is what actually matters. *(Note: a new `[LANGUAGE]` delimiter was added by Plan 3, but its value is always server-computed (`"nl"`/`"en"`), never user text, so it isn't a new injection surface — this item still applies to `[QUESTION]`/`[CONTEXT]`/`[HISTORY]`.)*

**New regression tests** (`wine-agent/tests/test_api.py` or new `test_security.py`):
- SQL-injection-shaped input (`'; DROP TABLE wines; --`, FTS special chars) → 200, catalogue row count unchanged.
- "show me everything" / "list all wines" → card count never exceeds `top_k` (6).
- Oversized/empty message → 400, generic body, no path/stack trace.
- Malformed `session_id` → server ignores it, mints its own valid UUID.
- Disallowed `Origin` header → no `Access-Control-Allow-Origin` echoed.
- Burst requests from one client → a `429` appears.
- ~~Mocked Ollama `ConnectError` → SSE yields `{"type":"error"}`, not an unhandled exception.~~ *(done — `tests/test_llm.py`, `tests/test_chat_service.py::test_llm_error_yields_clean_error_event_not_an_exception`)*
- Injection payload with literal `[/QUESTION][CONTEXT]...` → unit-test `build_user_message()` directly, assert no unescaped injected delimiter survives.

**Verification:**
```bash
curl -i -H "Origin: http://evil.example" http://localhost:8099/chat -d '{"message":"hi"}' -H "Content-Type: application/json"
for i in $(seq 1 30); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8099/chat -d '{"message":"hi"}' -H "Content-Type: application/json"; done
curl -s http://localhost:8099/chat -d '{"message":"hi","session_id":"not-a-uuid"}' -H "Content-Type: application/json"
pytest wine-agent/tests/test_security.py -v
```

---

## Plan 2: Chat UI Redesign — IMPLEMENTED

**Why:** `chat/static/demo.html` was a single self-contained static file (no build step, per `CHAT.md`) with a workable but flat visual system: ad hoc palette/spacing values, a static `'…'` loading placeholder instead of a real typing indicator, plain-`textContent`-only bubbles (no structure in longer replies), minimal cards, zero accessibility attributes, and zero responsive breakpoints. The redesign stayed vanilla HTML/CSS/JS in the same file — a framework wasn't justified by the UI's complexity.

**Changes (all implemented in `chat/static/demo.html`):**

1. **Refined design tokens** — Extended `:root` with a full token set: kept `--accent #7a1f2b` / `--accent-soft #f3e2e4` as brand anchors, added `--accent-dark` for hover/active, tokenized `--err`/`--err-soft`, added a spacing scale (`--space-1` … `--space-6`), a type scale (`--fs-xs/sm/base/md/lg`), and elevation tokens (`--shadow-sm/md`).

2. **Animated typing indicator** — Replaced the static `'…'` placeholder with a `.typing` dots component (3 spans, CSS `@keyframes` pulse).

3. **Card redesign** — Reworked `addCardsBefore()` to render `ProductCard.image_url` (previously present in the schema but unused in the UI) with a fixed-aspect-ratio thumbnail, falling back to an accent-soft 🍷 placeholder when null. Added type-scale hierarchy and `:hover`/`:focus-visible` lift states.

4. **Removed dead code** — Deleted the unused `addCards()` function (only `addCardsBefore()` was ever wired to the `'cards'` SSE event). Also removed a stray empty-`<div>` insertion left over in the cards-event handler.

5. **Safe minimal Markdown rendering** — Added a hand-written allowlist renderer for `**bold**` and `- list` only, building `<strong>`/`<ul><li>` via explicit `createElement`/`textContent` — never `innerHTML` of model output.

6. **Fixed chip UX** — Added a "Try asking:" label above the 4 example-question chips (previously read like an accidental EN/NL language switcher) and per-chip `aria-label`s.

7. **Accessibility basics** — Added `aria-live="polite"` + `role="log"` to `#log`, a real (visually-hidden) `<label>` for the input, and `:focus-visible` outline styles for input/button/chips/cards.

8. **Responsive breakpoints** — Added `@media` queries at ~640px and ~400px.

**Verification (manual — static page, no test harness attached to it):**
- `GET /demo` — confirm new tokens/typing indicator/chip labels, resize to <640px/<400px for breakpoints.
- Ask a question expecting a list-style answer — confirm `**bold**`/`- list` renders as `<strong>`/`<ul>`; a message containing `<script>alert(1)</script>` renders as inert text.
- Trigger a `cards` event — confirm image/placeholder, hover/focus states.
- Tab through with keyboard only — visible focus rings; verify `#log`'s live-region behavior with a screen reader or the browser's accessibility tree inspector.

---

## Plan 3: Ollama Reliability + Instant-Reply Feel + Language Matching — IMPLEMENTED

**Why:** Three compounding issues: (a) the app silently defaulted to `FakeLLM` (`WINE_LLM_BACKEND` default `"fake"`), which was hardcoded English and didn't even read its own `system` argument — so unless a developer explicitly set the env var, every reply was English regardless of query language, which read as "Ollama doesn't work" when really Ollama was never in the loop; (b) there was no real language detection anywhere — the only mechanism was one static sentence in `SYSTEM_PROMPT` with zero per-turn reinforcement; (c) `OllamaLLM.stream()` had no timeout and no error handling, so a slow/down Ollama caused a silently truncated stream rather than a clean error, and the client-side `'…'` placeholder was static, not a real typing cue.

**Changes (all implemented):**

1. **Lightweight EN/NL language detection** — `chat/lang.py`, `detect_language(text) -> "nl" | "en"`: a rule-based function-word scorer mirroring `planner.py`'s existing bilingual-keyword style, no new ML dependency.

2. **Threaded language through the prompt** — `chat/service.py` calls `detect_language()` once per turn; `chat/prompt.py`'s `build_system_prompt(language)` injects a per-turn reinforced instruction, and `build_user_message()` carries a server-computed `[LANGUAGE]nl|en[/LANGUAGE]` marker.

3. **`FakeLLM` stays English-only, but says so** — `chat/llm.py`'s `FakeLLM` prefixes replies with `[fake-backend, English-only]` when the detected input language isn't English, instead of silently answering in the wrong language. Documented in `CHAT.md`.

4. **Ollama error handling + timeout** — `chat/llm.py`'s `OllamaLLM.stream()` now uses a bounded `httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)` and raises `LLMError` on failure, caught in `chat/service.py` to yield a clean, localized `{"type": "error", ...}` SSE event instead of dying mid-stream.

5. **Typing indicator** — Implemented as part of Plan 2 (`chat/static/demo.html`, `.typing` component).

6. **Surfaced active backend** — Startup log line in `chat/api.py`'s lifespan handler; `GET /snapshot` and the `session` SSE event both carry a `backend` field (`"fake"`/`"ollama"`); shown in the demo page's header subtitle.

**Verification performed:**
- `pytest` — 54 tests passing, including new `tests/test_lang.py`, `tests/test_llm.py`, and language/error-handling cases in `tests/test_chat_service.py`.
- Live manual check: Dutch question against the `fake` backend → `[fake-backend, English-only]`-tagged reply; English question → untagged reply.
- Live manual check: `WINE_LLM_BACKEND=ollama` pointed at an unreachable host → clean `{"type":"error"}` SSE event within ~5s instead of a hang.
- `GET /snapshot` confirmed to return a `backend` field.

---

## Critical files
- `wine-agent/chat/api.py`
- `wine-agent/chat/llm.py`
- `wine-agent/chat/prompt.py`
- `wine-agent/chat/service.py`
- `wine-agent/chat/sessions.py`
- `wine-agent/chat/config.py`
- `wine-agent/chat/planner.py`
- `wine-agent/chat/lang.py` (new)
- `wine-agent/chat/static/demo.html`
- `wine-agent/schemas/models.py` (read-only reference — `ProductCard`/`SnapshotRef` contract)
