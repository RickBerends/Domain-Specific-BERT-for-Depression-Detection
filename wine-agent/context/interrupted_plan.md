# Add Groq as a fast, free LLM backend

## Context

The CPU-only Ollama testing earlier today established that this machine can't run `qwen2.5:7b-instruct` at interactive speed — 56s for "hello," and the full recommend-turn prompt never completed even at a 6-minute timeout. A follow-up breakdown of options (hardware, model size, free vs. paid, Phoenix integration) was presented, and the user picked **a free cloud API** over paid Claude or buying GPU hardware.

Between the two free options surveyed (Groq, Google AI Studio), this plan defaults to **Groq**: it doesn't train on free-tier prompts (Google's free tier does — a real concern for customer conversations), and its LPU hardware gives sub-200ms time-to-first-token — a dramatic, direct fix for the exact "doesn't feel native" complaint that started today's work. Groq's API is OpenAI-compatible, which also keeps a future Phoenix-tracing story simple (`openinference-instrumentation-openai` auto-traces any OpenAI-shaped client call, regardless of `base_url`) — though standing up Phoenix itself remains out of scope for this change, consistent with `CHAT.md`'s existing "not yet built" list.

`chat/llm.py`'s own module docstring already anticipated this: *"swapping to a hosted OpenAI-compatible endpoint later is a config change."* This plan is exactly that swap, following the same `LLMClient` Protocol pattern `FakeLLM`/`OllamaLLM` already establish — no changes needed anywhere outside `chat/llm.py`, `chat/config.py`, and `pyproject.toml`.

## Changes

1. **`pyproject.toml`**: add an optional extra `groq = ["openai>=1.0"]` (lazy-imported inside the method, mirroring how `OllamaLLM.stream()` does `import httpx` locally rather than at module level — keeps the base install free of a dependency most runs won't use).

2. **`chat/llm.py`**: new `GroqLLM` class implementing `LLMClient`:
    - Constructed with `api_key`, `model`, `read_timeout` (default a modest ~30s — Groq is fast, no need for the multi-minute headroom `OllamaLLM` needed for CPU inference).
    - `stream(system, user)`: lazily `import openai`, construct `openai.OpenAI(api_key=self.api_key, base_url="https://api.groq.com/openai/v1", timeout=self.read_timeout)`, call `client.chat.completions.create(model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], stream=True)`, and yield `chunk.choices[0].delta.content` for each chunk where content is non-empty.
    - Wrap the call in `try/except openai.APIError` (the SDK's common base for HTTP-status and connection failures) → raise `LLMError`, matching `OllamaLLM`'s existing `except httpx.HTTPError` pattern exactly.

3. **`chat/config.py`**:
    - `groq_api_key: str = os.getenv("GROQ_API_KEY", "")`
    - `groq_model: str = os.getenv("WINE_GROQ_MODEL", "llama-3.1-8b-instant")` — the 8B model, not 70B: matches the "7-8B is already enough for this grounded, short-form task" conclusion from the earlier breakdown, and has a much higher free-tier daily allowance (14,400 RPD vs. 1,000 RPD for `llama-3.3-70b-versatile`). Document the 70B model as the quality-upgrade knob (same tiering pattern as Haiku→Sonnet) via `WINE_GROQ_MODEL`.
    - `groq_read_timeout: float = float(os.getenv("WINE_GROQ_READ_TIMEOUT", "30"))`

4. **`chat/llm.py`'s `build_llm(cfg)`**: add `elif cfg.llm_backend == "groq": return GroqLLM(cfg.groq_api_key, cfg.groq_model, read_timeout=cfg.groq_read_timeout)`.

5. **`CHAT.md`**: document the new backend — `WINE_LLM_BACKEND=groq` + `GROQ_API_KEY`, note the free-tier rate limits (30 RPM / 1,000 RPD for the 70B model; the 8B default has much higher daily volume), and that Groq is a genuinely free, fast, cloud-hosted alternative to local Ollama for this exact machine's CPU constraint. Note the token-per-minute ceiling (12K TPM on the 70B tier) as something to watch if traffic grows — our per-exchange prompt (system + 3 annotated products + history) runs a few hundred to ~1500 tokens, so this comfortably supports interactive use at low-to-moderate volume.

## Verification

- `pytest` — new test in `tests/test_llm.py` mirroring the existing `OllamaLLM` connect-failure test: monkeypatch `openai.OpenAI` to raise, assert `GroqLLM.stream()` raises `LLMError`.
- Live: sign up for a free Groq API key (console.groq.com), `pip install -e ".[groq]"`, set `GROQ_API_KEY` + `WINE_LLM_BACKEND=groq`, restart the server, and reproduce today's exact anniversary-dinner conversation (`"I need a wine for my anniversary dinner"` → clarifying question → `"Something bold and robust to pair with steak"` → 3 curated cards + favourite-pick reply) — this time expect the full round trip in low seconds, not minutes, confirming the CPU bottleneck is actually resolved.
- Confirm `GET /snapshot`'s `backend` field reports `"groq"` and the demo page's fake-backend banner does not appear.

## Explicitly out of scope
Standing up Phoenix itself (Docker container, `arize-phoenix-otel` registration, the `openinference-instrumentation-openai` auto-instrumentor) is not part of this change — `CHAT.md` already lists real Phoenix evals as "not yet built," and this plan only gets the LLM backend itself working quickly and cheaply. The Groq choice keeps that future work smaller (OpenAI-shaped client = free auto-tracing later), but doesn't do it now.
