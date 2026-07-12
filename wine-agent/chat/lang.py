"""Cheap, rule-based EN/NL language detection for the incoming user message.

Mirrors the bilingual-keyword approach already used in ``chat.planner`` rather
than pulling in a language-ID dependency: with only two target languages, a
small function-word scorer is accurate enough, and it keeps the project's
"no ML dependency for text heuristics" style consistent. Worst case on a
misclassification is a reply in the wrong (but still supported) language, not
a broken one.
"""

from __future__ import annotations

import re
from typing import Literal

Language = Literal["nl", "en"]

LANGUAGE_NAMES: dict[Language, str] = {"nl": "Dutch", "en": "English"}

# High-signal function words that rarely appear in the other language.
_NL_WORDS = frozenset(
    """
    de het een en van voor met is zijn hebben wat welke wat welk hoe waar
    wanneer waarom wie deze die dit dat niet geen ook nog wel maar of dan
    goedkope goedkoper duurdere rode witte wijn wijnen euro's fles flessen
    graag alsjeblieft alstublieft kunt kun jullie hebben jullie
    """.split()
)
_EN_WORDS = frozenset(
    """
    the a an and of for with is are have what which how where when why who
    this that these those not no also but or than cheap cheaper expensive
    red white wine wines bottle bottles please could you your do does
    """.split()
)

_WORD_RE = re.compile(r"[a-zà-ÿ']+")


def detect_language(text: str) -> Language:
    """Guess whether ``text`` is Dutch or English. Defaults to English."""
    tokens = _WORD_RE.findall((text or "").lower())
    nl_score = sum(1 for tok in tokens if tok in _NL_WORDS)
    en_score = sum(1 for tok in tokens if tok in _EN_WORDS)
    if nl_score > en_score:
        return "nl"
    return "en"
