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
    "white": ColorType.white,
    "rosé": ColorType.rose,
    "rose": ColorType.rose,
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
    "mousserend": ColorType.sparkling,
    "mousserende": ColorType.sparkling,
    "bubbels": ColorType.sparkling,
    "dessertwijn": ColorType.dessert,
}

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
    tokens = re.findall(r"[a-zà-ÿ0-9]+", text)
    for tok in tokens:
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
