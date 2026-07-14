"""Snapshot contract models (technical plan §4.3, addendum §3).

These are deliberately source-agnostic. Prices are stored as integer cents to
avoid float drift and to absorb the client site's Dutch decimal-comma format
(addendum §3) at the ingest boundary. Stock is an enum plus an optional count
so textual stock ("Nog 6 flessen beschikbaar") round-trips losslessly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ColorType(str, Enum):
    red = "red"
    white = "white"
    rose = "rose"
    sparkling = "sparkling"
    dessert = "dessert"
    fortified = "fortified"


class StockStatus(str, Enum):
    in_stock = "in_stock"
    limited = "limited"
    out = "out"


class Language(str, Enum):
    en = "en"
    nl = "nl"


class PriceTier(BaseModel):
    """A quantity-dependent price (addendum §3: "Vanaf 12 flessen 10,95 per fles")."""

    min_qty: int = Field(1, ge=1)
    price_cents: int = Field(..., ge=0)
    unit: str = "fles"  # fles | doos | wijnpakket


class Product(BaseModel):
    slug: str
    name: str
    producer: str | None = None
    grape_varieties: list[str] = Field(default_factory=list)
    region: str | None = None
    country: str | None = None
    color_type: ColorType | None = None
    vintage: int | None = None
    abv: float | None = Field(None, ge=0, le=100)
    volume_ml: int | None = Field(None, ge=0)

    price_cents: int | None = Field(None, ge=0)
    currency: str = "EUR"
    price_tiers: list[PriceTier] = Field(default_factory=list)
    offer_valid_until: datetime | None = None

    stock_status: StockStatus = StockStatus.in_stock
    stock_count: int | None = Field(None, ge=0)

    tasting_notes: str | None = None
    food_pairing: list[str] = Field(default_factory=list)
    language: Language = Language.en

    image_url: HttpUrl | None = None
    url: HttpUrl | None = None
    scraped_at: datetime | None = None

    @field_validator("grape_varieties", "food_pairing", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> object:
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def price_eur(self) -> float | None:
        return None if self.price_cents is None else self.price_cents / 100

    def retrieval_card(self) -> str:
        """The single dense text chunk indexed per product (technical plan §4.5)."""
        bits: list[str] = []
        if self.vintage:
            bits.append(str(self.vintage))
        bits.append(self.name)
        if self.color_type:
            bits.append(f"({self.color_type.value})")
        if self.region:
            bits.append(f"from {self.region}")
        if self.grape_varieties:
            bits.append("grapes: " + ", ".join(self.grape_varieties))
        if self.price_eur is not None:
            bits.append(f"€{self.price_eur:.2f}")
        if self.food_pairing:
            bits.append("pairs with: " + ", ".join(self.food_pairing))
        if self.tasting_notes:
            bits.append(self.tasting_notes)
        return " — ".join(bits)


class Content(BaseModel):
    """A non-product page: FAQ, delivery, glossary, pairing guide (technical plan §4.3)."""

    url: HttpUrl | None = None
    title: str
    section: str | None = None
    body_text: str
    language: Language = Language.en
    last_modified: datetime | None = None


class ProductCard(BaseModel):
    """The structured side-channel the widget renders (technical plan §5.1 step 7).

    Extracted from retrieved metadata so the LLM never formats a price itself.
    """

    slug: str
    name: str
    price_eur: float | None = None
    currency: str = "EUR"
    image_url: str | None = None
    url: str | None = None
    # Real product facts (already on Product, just not previously surfaced on
    # the card) — no fabricated ratings/awards/review data is ever added here.
    grape_varieties: list[str] = Field(default_factory=list)
    country: str | None = None
    vintage: int | None = None
    stock_status: StockStatus = StockStatus.in_stock
    # True when this card didn't fully satisfy the customer's stated filters
    # and the retriever had to relax them to find anything (chat.retriever
    # .RetrievalResult.relaxed) — lets the UI show an honest "closest
    # alternative" badge instead of presenting it as a perfect match.
    closest_alternative: bool = False
    # Curated recommendation slot (chat.retriever.select_recommendations):
    # "best_match" | "best_value" | "different" | None, with a short,
    # data-driven reason — never LLM-generated.
    role: str | None = None
    reason: str | None = None

    @classmethod
    def from_product(
        cls,
        p: Product,
        closest_alternative: bool = False,
        role: str | None = None,
        reason: str | None = None,
        fallback_image_url: str | None = None,
    ) -> "ProductCard":
        return cls(
            slug=p.slug,
            name=p.name,
            price_eur=p.price_eur,
            currency=p.currency,
            image_url=str(p.image_url) if p.image_url else fallback_image_url,
            url=str(p.url) if p.url else None,
            grape_varieties=p.grape_varieties[:2],
            country=p.country,
            vintage=p.vintage,
            stock_status=p.stock_status,
            closest_alternative=closest_alternative,
            role=role,
            reason=reason,
        )


class SnapshotRef(BaseModel):
    """Identifies a published snapshot (technical plan §2 separation contract).

    The chat service binds to one of these; the ingest side flips ``published``
    to true only after validation gates pass (§4.4).
    """

    snapshot_id: str
    created_at: datetime
    published: bool = False
    product_count: int = 0
    content_count: int = 0
