"""Prompt-injection defenses (technical plan §7).

The agent's strongest protection is architectural: it is **read-only** — it has
no tools and no side effects, so a hijacked model can only emit text, never act.
This module adds the structural layer on top:

1. ``neutralize`` — strip the prompt's structural delimiters out of every piece
   of *untrusted* text (the user's message, conversation history, and retrieved
   catalogue/content, which may eventually come from a crawled site). Without
   this, a user typing ``[/CONTEXT]`` or a forged instruction tag could break
   out of the data block and pose as the system.
2. ``looks_like_injection`` — a light EN/NL heuristic used only for
   observability (a span attribute / metric). It intentionally does **not**
   reject input: blocklists produce false positives, and neutralization plus the
   read-only design already contain the risk. It just flags turns worth review.
"""

from __future__ import annotations

import re

# Structural tags the prompt uses, plus role tokens an attacker might forge to
# impersonate the system/assistant. Matched case-insensitively, with optional
# leading slash and trailing attributes, in square OR angle brackets.
_RESERVED = r"(?:context|question|history|system|assistant|user|inst|/inst|s|/s)"
_TAG_RE = re.compile(rf"[\[<]\s*/?\s*{_RESERVED}\b[^\]>]*[\]>]", re.IGNORECASE)

# Common instruction-override phrasings (EN + NL). Observability only.
_INJECTION_RE = re.compile(
    r"""
    ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?)
  | disregard\s+(?:all\s+|the\s+)?(?:previous|above|prior)
  | forget\s+(?:everything|all|your\s+instructions)
  | (?:reveal|show|print|repeat)\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?(?:prompt|instructions?)
  | you\s+are\s+now\b
  | new\s+instructions?\s*:
  | (?:act|behave)\s+as\s+(?:if|though|a)\b
  | negeer\s+(?:alle\s+|de\s+)?(?:vorige|voorgaande|bovenstaande)\s+(?:instructies|opdrachten)
  | vergeet\s+(?:alles|je\s+instructies)
  | (?:toon|laat\s+zien|herhaal)\s+(?:je\s+)?(?:systeem)?(?:prompt|instructies)
  | je\s+bent\s+nu\b
  | nieuwe\s+instructies?\s*:
    """,
    re.IGNORECASE | re.VERBOSE,
)


def neutralize(text: str) -> str:
    """Defang prompt-structure tokens in untrusted text.

    Reserved tags like ``[/CONTEXT]`` or ``<system>`` are rewritten to an inert
    parenthesised form so they read as ordinary words and can no longer forge a
    section boundary. The text's meaning for a human is preserved.
    """
    if not text:
        return text

    def _defang(match: re.Match) -> str:
        inner = match.group(0)[1:-1].strip()
        return f"({inner})"

    return _TAG_RE.sub(_defang, text)


def looks_like_injection(text: str) -> bool:
    """True if the text resembles an instruction-override attempt (telemetry)."""
    return bool(text) and _INJECTION_RE.search(text) is not None
