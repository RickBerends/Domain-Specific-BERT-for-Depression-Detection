"""Online chat subsystem (technical plan §5).

Retrieve → generate → stream. Reads only a published snapshot; never triggers
ingest. Backends (embedder, vector index, LLM) are swappable via ``config`` so
the whole slice runs offline with deterministic fakes and switches to Ollama
with environment variables only.
"""
