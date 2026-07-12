"""Deterministic placeholder illustrations for products with no real photo.

Roughly 100 of the 120 catalogue products (everything sourced from X-Wines)
have no image at all — that dataset has no image column. The 20 seed products
that do have one point at a non-resolving fixture domain
(``fixtureshop.example``). Rather than leaving cards blank or pretending a
fake URL is real photography, generate a stable, clearly-labeled placeholder
per colour so cards always have *something* visual — never presented as an
actual bottle photo.
"""

from __future__ import annotations

from schemas import ColorType, Product

# (background, foreground) hex, no leading '#' — placehold.co's URL format.
_COLORS: dict[ColorType, tuple[str, str]] = {
    ColorType.red: ("7a1f2b", "ffffff"),
    ColorType.white: ("f3e2e4", "2b2320"),
    ColorType.rose: ("e8b4b8", "2b2320"),
    ColorType.sparkling: ("d4af37", "2b2320"),
    ColorType.dessert: ("8b5a2b", "ffffff"),
    ColorType.fortified: ("4a2511", "ffffff"),
}
_DEFAULT_COLORS = ("8a7f78", "ffffff")


def placeholder_image_url(base_url: str, product: Product) -> str:
    bg, fg = _COLORS.get(product.color_type, _DEFAULT_COLORS)
    label = product.color_type.value.title() if product.color_type else "Wine"
    text = f"{label}+Wine".replace(" ", "+")
    return f"{base_url.rstrip('/')}/300x400/{bg}/{fg}?text={text}"


# Sample bottle photos dropped into /img (served via the static mount in
# chat.api), one per curated recommendation slot (chat.retriever
# .select_recommendations). These are real photos of specific bottles, not
# the actually-recommended product, so they're only used for the three
# curated card roles — never claimed to be a photo of that exact wine, just
# card art for the slot.
ROLE_SAMPLE_IMAGES: dict[str, str] = {
    "best_match": "/img/img1.png",
    "best_value": "/img/img2.png",
    "different": "/img/img3.png",
}


def card_image_url(base_url: str, product: Product, role: str | None) -> str:
    """The image to show on a card: the product's real photo if it has one,
    else the role's sample bottle photo, else a generated placeholder."""
    if role in ROLE_SAMPLE_IMAGES:
        return ROLE_SAMPLE_IMAGES[role]
    return placeholder_image_url(base_url, product)
