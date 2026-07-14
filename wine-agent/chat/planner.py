"""Query planning (technical plan §5.1 step 3): cheap, rule-based, EN + NL.

Extracts metadata filters (color, price bounds, country) and routes the turn to
``catalog`` or ``policy``. Filters let "red under €15" become a metadata filter
instead of a vector guess (§4.5); the taste-style/pairing vocabulary stays with
the retriever's text search. A small LLM can replace this later behind the same
``plan()`` signature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemas import ColorType

_COLOR_WORDS: dict[str, ColorType] = {
    # EN
    "red": ColorType.red,
    "reds": ColorType.red,
    "white": ColorType.white,
    "whites": ColorType.white,
    "rosé": ColorType.rose,
    "rose": ColorType.rose,
    "roses": ColorType.rose,
    "rosés": ColorType.rose,
    "sparkling": ColorType.sparkling,
    "bubbles": ColorType.sparkling,
    "champagne": ColorType.sparkling,
    "cava": ColorType.sparkling,
    "prosecco": ColorType.sparkling,
    "dessert": ColorType.dessert,
    "port": ColorType.fortified,
    "fortified": ColorType.fortified,
    # NL
    "rood": ColorType.red,
    "rode": ColorType.red,
    "wit": ColorType.white,
    "witte": ColorType.white,
    "witwijn": ColorType.white,
    "roodwijn": ColorType.red,
    "mousserend": ColorType.sparkling,
    "mousserende": ColorType.sparkling,
    "bubbels": ColorType.sparkling,
    "dessertwijn": ColorType.dessert,
    # Grape/varietal names that are unambiguously one colour. Ambiguous ones
    # (bare "pinot", "moscato" — which span multiple colours/styles) are
    # deliberately excluded rather than guessed.
    "chardonnay": ColorType.white,
    "riesling": ColorType.white,
    "viognier": ColorType.white,
    "albariño": ColorType.white,
    "albarino": ColorType.white,
    "verdejo": ColorType.white,
    "godello": ColorType.white,
    "vermentino": ColorType.white,
    "assyrtiko": ColorType.white,
    "gewürztraminer": ColorType.white,
    "gewurztraminer": ColorType.white,
    "trebbiano": ColorType.white,
    "grüner": ColorType.white,
    "gruner": ColorType.white,
    "merlot": ColorType.red,
    "malbec": ColorType.red,
    "syrah": ColorType.red,
    "shiraz": ColorType.red,
    "tempranillo": ColorType.red,
    "sangiovese": ColorType.red,
    "zinfandel": ColorType.red,
    "nebbiolo": ColorType.red,
    "grenache": ColorType.red,
    "garnacha": ColorType.red,
    "carignan": ColorType.red,
    "mourvèdre": ColorType.red,
    "mourvedre": ColorType.red,
    "cabernet": ColorType.red,
}

# Two-word varietal phrases where the single first token would be ambiguous
# on its own (e.g. bare "pinot" or "sauvignon").
_COLOR_PHRASES: dict[tuple[str, str], ColorType] = {
    ("sauvignon", "blanc"): ColorType.white,
    ("pinot", "grigio"): ColorType.white,
    ("pinot", "gris"): ColorType.white,
    ("pinot", "blanc"): ColorType.white,
    ("pinot", "noir"): ColorType.red,
    ("cabernet", "sauvignon"): ColorType.red,
    ("cabernet", "franc"): ColorType.red,
}

# Negation cues (EN + NL). Colour/varietal words within a short window after
# one of these are excluded from candidacy, so "not red, I'd like white"
# doesn't lock onto "red" just because it appears first in the sentence.
_NEGATION_WORDS = frozenset({"not", "no", "dont", "isnt", "aint", "niet", "geen"})
_NEGATION_WINDOW = 3

_COUNTRY_WORDS: dict[str, str] = {
    "france": "France", "french": "France", "frankrijk": "France", "franse": "France",
    "italy": "Italy", "italian": "Italy", "italie": "Italy", "italië": "Italy", "italiaanse": "Italy",
    "spain": "Spain", "spanish": "Spain", "spanje": "Spain", "spaanse": "Spain",
    "germany": "Germany", "german": "Germany", "duitsland": "Germany", "duitse": "Germany",
    "portugal": "Portugal", "portuguese": "Portugal", "portugese": "Portugal",
    "netherlands": "Netherlands", "dutch": "Netherlands", "nederland": "Netherlands",
    "nederlandse": "Netherlands", "hollandse": "Netherlands",
    "argentina": "Argentina", "argentinian": "Argentina", "argentinie": "Argentina",
    "argentinië": "Argentina", "argentijnse": "Argentina",
    "australia": "Australia", "australian": "Australia", "australie": "Australia",
    "australië": "Australia", "australische": "Australia",
    "austria": "Austria", "austrian": "Austria", "oostenrijk": "Austria", "oostenrijkse": "Austria",
    "chile": "Chile", "chilean": "Chile", "chili": "Chile", "chileense": "Chile",
    "brazil": "Brazil", "brazilian": "Brazil", "brazilie": "Brazil", "braziliaanse": "Brazil",
}

_POLICY_WORDS = (
    # EN
    "opening hours", "open on", "delivery", "shipping", "return", "refund",
    "withdrawal", "contact", "phone", "email", "age", "18", "privacy",
    # NL
    "openingstijden", "geopend", "bezorging", "bezorgen", "verzending",
    "verzendkosten", "retour", "herroeping", "terugsturen", "leeftijd",
    "telefoonnummer", "contactgegevens",
)

# "under €15", "below 15 euros", "tot 15 euro", "onder de €12,50", "max 15"
_MAX_PRICE_RE = re.compile(
    r"(?:under|below|less than|cheaper than|max(?:imum)?|up to|tot|onder(?: de)?|"
    r"hoogstens|maximaal|voor minder dan)\s*(?:€|eur\b)?\s*(\d+(?:[.,]\d{1,2})?)\s*(?:€|euro?s?\b)?",
    re.IGNORECASE,
)
# "over €20", "at least 15", "vanaf 20 euro", "minstens 15"
_MIN_PRICE_RE = re.compile(
    r"(?:over|above|more than|at least|from|vanaf|minstens|minimaal|duurder dan)\s*"
    r"(?:€|eur\b)?\s*(\d+(?:[.,]\d{1,2})?)\s*(?:€|euro?s?\b)?",
    re.IGNORECASE,
)

_CHEAPER_WORDS = ("cheaper", "less expensive", "goedkoper", "goedkopere", "voordeliger")

# Generic "just pick something for me" phrasing, with no specific wine name,
# grape, region, or style cue — distinct from e.g. "Chianti Classico" or "do
# you have a Chianti?", which have no colour/price/country filter either but
# ARE specific (a proper-noun wine name) and retrieve just fine as-is.
_VAGUE_SIGNAL_WORDS = (
    # EN
    "wine", "bottle", "something", "recommend", "recommendation", "suggest",
    "suggestion", "help", "gift", "occasion", "need", "looking",
    # NL
    "wijn", "fles", "iets", "aanraden", "aanbeveling", "advies", "adviseer",
    "hulp", "cadeau", "gelegenheid", "nodig", "zoek",
)


def is_vague_request(message: str) -> bool:
    """True for generic requests with no specific wine/grape/region name or
    style cue — used by ``chat.service.ChatService`` to ask a clarifying
    question instead of guessing, rather than every filter-less message
    (which would also wrongly catch specific searches like "Chianti
    Classico").
    """
    tokens = re.findall(r"[a-zà-ÿ0-9]+", message.lower().replace("'", ""))
    return any(tok in _VAGUE_SIGNAL_WORDS for tok in tokens)


@dataclass
class Filters:
    color_type: ColorType | None = None
    country: str | None = None
    max_price_cents: int | None = None
    min_price_cents: int | None = None

    def any(self) -> bool:
        return any(
            v is not None
            for v in (self.color_type, self.country, self.max_price_cents, self.min_price_cents)
        )


@dataclass
class Plan:
    route: str  # catalog | policy
    filters: Filters = field(default_factory=Filters)
    wants_cheaper: bool = False


def _to_cents(raw: str) -> int:
    return round(float(raw.replace(",", ".")) * 100)


def plan(message: str) -> Plan:
    text = message.lower()

    if any(w in text for w in _POLICY_WORDS):
        return Plan(route="policy")

    filters = Filters()
    # Apostrophes are dropped before tokenizing so contractions ("don't",
    # "isn't") collapse to the negation cues in _NEGATION_WORDS.
    tokens = re.findall(r"[a-zà-ÿ0-9]+", text.replace("'", ""))

    negated = set()
    for i, tok in enumerate(tokens):
        if tok in _NEGATION_WORDS:
            negated.update(range(i + 1, min(i + 1 + _NEGATION_WINDOW, len(tokens))))

    for i in range(len(tokens) - 1):
        if i in negated or (i + 1) in negated:
            continue
        color = _COLOR_PHRASES.get((tokens[i], tokens[i + 1]))
        if color is not None and filters.color_type is None:
            filters.color_type = color

    for i, tok in enumerate(tokens):
        if i in negated:
            continue
        if filters.color_type is None and tok in _COLOR_WORDS:
            filters.color_type = _COLOR_WORDS[tok]
        if filters.country is None and tok in _COUNTRY_WORDS:
            filters.country = _COUNTRY_WORDS[tok]

    if m := _MAX_PRICE_RE.search(text):
        filters.max_price_cents = _to_cents(m.group(1))
    if m := _MIN_PRICE_RE.search(text):
        # avoid the same number matching both ("from 10 to 15" edge): min must differ
        cents = _to_cents(m.group(1))
        if cents != filters.max_price_cents:
            filters.min_price_cents = cents

    wants_cheaper = any(w in text for w in _CHEAPER_WORDS)
    return Plan(route="catalog", filters=filters, wants_cheaper=wants_cheaper)
