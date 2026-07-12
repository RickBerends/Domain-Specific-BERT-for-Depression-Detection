"""Observability seam (technical plan §5.5).

Phoenix/OpenTelemetry is optional: if the packages aren't installed (the
default in this slice), spans degrade to a no-op so the service runs anywhere.
When ``opentelemetry`` is present the same calls emit real spans that Arize
Phoenix can ingest. This keeps the tracing call sites in the service code
identical whether or not Phoenix is wired up.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

try:  # pragma: no cover - exercised only when otel is installed
    from opentelemetry import trace

    _tracer = trace.get_tracer("wine-agent.chat")
    _OTEL = True
except Exception:  # ImportError or misconfiguration → no-op tracing
    _tracer = None
    _OTEL = False


class _NoopSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: D401
        return None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    if not _OTEL:
        yield _NoopSpan()
        return
    with _tracer.start_as_current_span(name) as sp:  # pragma: no cover
        for key, value in attributes.items():
            sp.set_attribute(key, value)
        yield sp


def tracing_enabled() -> bool:
    return _OTEL
