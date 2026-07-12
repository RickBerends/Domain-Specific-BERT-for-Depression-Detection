"""LLM abstraction: deterministic fake (default) + streaming Ollama impl.

Both expose ``stream(system, user) -> Iterator[str]`` so the chat service is
identical regardless of backend. The fake is grounded-by-construction: it only
ever emits text built from the context it was given, so tests can assert
grounding without a model, and CI needs no 5 GB download. The Ollama impl
streams ``/api/chat`` — swapping to a hosted OpenAI-compatible endpoint later is
a config change (technical plan §5.4).
"""

from __future__ import annotations

from typing import Iterator, Protocol


class LLMClient(Protocol):
    def stream(self, system: str, user: str) -> Iterator[str]: ...


class FakeLLM:
    """Emits a short grounded answer assembled from the retrieved context block.

    It extracts the ``[CONTEXT]`` section the prompt builder embeds in ``user``
    and paraphrases the first lines. Never invents facts — if context is empty
    it emits the grounded-refusal line (technical plan §5.2).
    """

    REFUSAL = (
        "I couldn't find that in the shop's information. "
        "Please contact the shop directly and they'll be glad to help."
    )

    def stream(self, system: str, user: str) -> Iterator[str]:
        context = self._extract_context(user)
        if not context:
            yield from self._chunk(self.REFUSAL)
            return
        first = context[0]
        answer = f"Based on the shop's catalogue: {first}"
        if len(context) > 1:
            answer += f" You might also like: {context[1]}"
        yield from self._chunk(answer)

    @staticmethod
    def _extract_context(user: str) -> list[str]:
        lines: list[str] = []
        in_ctx = False
        for raw in user.splitlines():
            line = raw.strip()
            if line == "[CONTEXT]":
                in_ctx = True
                continue
            if line == "[/CONTEXT]":
                break
            if in_ctx and line and not line.startswith("("):
                lines.append(line.lstrip("- ").strip())
        return lines

    @staticmethod
    def _chunk(text: str) -> Iterator[str]:
        for word in text.split(" "):
            yield word + " "


class OllamaLLM:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

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
        with httpx.Client(timeout=None) as client:
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


def build_llm(cfg) -> LLMClient:
    if cfg.llm_backend == "ollama":
        return OllamaLLM(cfg.ollama_base_url, cfg.ollama_llm_model)
    return FakeLLM()
