"""Deterministic placeholder-image generation for products with no photo."""

from __future__ import annotations

from schemas import ColorType, Product

from chat.images import ROLE_SAMPLE_IMAGES, card_image_url, placeholder_image_url


def _product(color: ColorType | None) -> Product:
    return Product(slug="x", name="x", color_type=color)


def test_placeholder_url_is_stable_and_color_coded():
    url_red = placeholder_image_url("https://placehold.co", _product(ColorType.red))
    url_white = placeholder_image_url("https://placehold.co", _product(ColorType.white))
    assert url_red.startswith("https://placehold.co/300x400/7a1f2b/")
    assert url_white.startswith("https://placehold.co/300x400/f3e2e4/")
    assert url_red != url_white
    # Deterministic: same input always produces the same URL.
    assert url_red == placeholder_image_url("https://placehold.co", _product(ColorType.red))


def test_placeholder_url_handles_missing_color():
    url = placeholder_image_url("https://placehold.co", _product(None))
    assert url.startswith("https://placehold.co/300x400/8a7f78/")


def test_placeholder_base_url_trailing_slash_is_handled():
    url = placeholder_image_url("https://placehold.co/", _product(ColorType.red))
    assert "//300x400" not in url


def test_card_image_url_uses_role_sample_photo():
    for role, expected in ROLE_SAMPLE_IMAGES.items():
        assert card_image_url("https://placehold.co", _product(ColorType.red), role) == expected


def test_card_image_url_falls_back_to_placeholder_without_a_role():
    url = card_image_url("https://placehold.co", _product(ColorType.white), None)
    assert url.startswith("https://placehold.co/300x400/f3e2e4/")
