"""LLM abstraction: deterministic fake (default) + streaming Ollama/Groq impls.

All backends expose ``stream(system, user) -> Iterator[str]`` so the chat
service is identical regardless of backend. The fake is grounded-by-
construction: it only ever emits text built from the context it was given, so
tests can assert grounding without a model, and CI needs no 5 GB download.
The Ollama impl streams its native ``/api/chat``. Groq's API is OpenAI-
compatible, so ``GroqLLM`` uses the official ``openai`` client pointed at
Groq's ``base_url`` — the "swap to a hosted OpenAI-compatible endpoint" the
module used to only anticipate (technical plan §5.4).
"""

from __future__ import annotations

import re
from typing import Iterator, Protocol


class LLMClient(Protocol):
    def stream(self, system: str, user: str) -> Iterator[str]: ...


class LLMError(Exception):
    """Raised when the configured LLM backend fails to produce a reply."""


_LANGUAGE_RE = re.compile(r"\[LANGUAGE\](\w+)\[/LANGUAGE\]")


class FakeLLM:
    """Emits a short grounded answer assembled from the retrieved context block.

    It extracts the ``[CONTEXT]`` section the prompt builder embeds in ``user``
    and paraphrases the first lines. Never invents facts — if context is empty
    it emits the grounded-refusal line (technical plan §5.2).

    This stub is deliberately English-only (translating is not worth the
    complexity for a deterministic test double). When the message was written
    in another supported language, it prefixes its reply with a visible tag
    instead of silently answering in the wrong language, so a developer who
    forgot to set ``WINE_LLM_BACKEND=ollama`` sees why replies aren't in Dutch.
    """

    REFUSAL = (
        "I couldn't find that in the shop's information. "
        "Please contact the shop directly and they'll be glad to help."
    )
    NON_ENGLISH_TAG = "[fake-backend, English-only] "
    CLOSEST_ALTERNATIVE_NOTE = (
        "Nothing matched your exact request, so here's the closest alternative: "
    )

    def stream(self, system: str, user: str) -> Iterator[str]:
        prefix = self.NON_ENGLISH_TAG if self._extract_language(user) != "en" else ""
        context, relaxed = self._extract_context(user)
        if not context:
            yield from self._chunk(prefix + self.REFUSAL)
            return
        caveat = self.CLOSEST_ALTERNATIVE_NOTE if relaxed else ""
        first = context[0]
        answer = f"{prefix}{caveat}Based on the shop's catalogue: {first}"
        if len(context) > 1:
            answer += f" You might also like: {context[1]}"
        yield from self._chunk(answer)

    @staticmethod
    def _extract_language(user: str) -> str:
        m = _LANGUAGE_RE.search(user)
        return m.group(1) if m else "en"

    @staticmethod
    def _extract_context(user: str) -> tuple[list[str], bool]:
        """Pull product/content lines out of ``[CONTEXT]``, and report whether
        the "closest alternatives" advisory (``chat.prompt.build_user_message``)
        was present — i.e. the filters had to be relaxed to find anything.

        The block always opens with one fixed boilerplate parenthesized line
        ("untrusted shop data..."); a *second* parenthesized line is the
        relaxation advisory. Earlier this method skipped every parenthesized
        line, which silently hid that advisory from the fake backend.
        """
        lines: list[str] = []
        in_ctx = False
        seen_boilerplate = False
        relaxed = False
        for raw in user.splitlines():
            line = raw.strip()
            if line == "[CONTEXT]":
                in_ctx = True
                continue
            if line == "[/CONTEXT]":
                break
            if not in_ctx or not line:
                continue
            if line.startswith("("):
                if not seen_boilerplate:
                    seen_boilerplate = True
                else:
                    relaxed = True
                continue
            lines.append(line.lstrip("- ").strip())
        return lines, relaxed

    @staticmethod
    def _chunk(text: str) -> Iterator[str]:
        for word in text.split(" "):
            yield word + " "


class OllamaLLM:
    def __init__(self, base_url: str, model: str, read_timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.read_timeout = read_timeout

    def stream(self, system: str, user: str) -> Iterator[str]:
        import json

        import httpx

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
        }
        # connect stays short — an unreachable Ollama should fail fast. read is
        # the gap between successive stream chunks, not total request time,
        # but CPU-only inference can still leave long silent gaps mid-prompt
        # -eval — 120s default headroom, configurable (WINE_OLLAMA_READ_TIMEOUT)
        # for slower hardware.
        timeout = httpx.Timeout(connect=5.0, read=self.read_timeout, write=5.0, pool=5.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if data.get("done"):
                            break
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama backend unreachable or errored: {exc}") from exc


class GroqLLM:
    """Groq's hosted inference — OpenAI-compatible, so this is the official
    ``openai`` client pointed at Groq's base_url rather than a bespoke HTTP
    call. Free tier, cloud-hosted, no local hardware needed — the fix for
    CPU-only Ollama being too slow for interactive chat on modest hardware.
    """

    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, model: str, read_timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model
        self.read_timeout = read_timeout

    def stream(self, system: str, user: str) -> Iterator[str]:
        import openai

        client = openai.OpenAI(
            api_key=self.api_key, base_url=self.BASE_URL, timeout=self.read_timeout
        )
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                stream=True,
            )
            for chunk in response:
                token = chunk.choices[0].delta.content
                if token:
                    yield token
        except openai.APIError as exc:
            raise LLMError(f"Groq backend unreachable or errored: {exc}") from exc


def build_llm(cfg) -> LLMClient:
    if cfg.llm_backend == "ollama":
        return OllamaLLM(
            cfg.ollama_base_url, cfg.ollama_llm_model, read_timeout=cfg.ollama_read_timeout
        )
    if cfg.llm_backend == "groq":
        return GroqLLM(cfg.groq_api_key, cfg.groq_model, read_timeout=cfg.groq_read_timeout)
    return FakeLLM()
