"""Prompt assembly: curated-role annotations and the clarifying-question prompt."""

from __future__ import annotations

from schemas import ColorType, Product

from chat.prompt import build_clarifying_system_prompt, build_user_message
from chat.retriever import Recommendation, RetrievalResult


def _product(slug: str) -> Product:
    return Product(slug=slug, name=slug, color_type=ColorType.red, price_cents=1000)


def test_build_user_message_annotates_curated_roles():
    products = [_product("a"), _product("b")]
    result = RetrievalResult(products=products)
    recommendations = [
        Recommendation(products[0], "best_match", "Closest match to your request"),
        Recommendation(products[1], "best_value", "The best-value option that still fits your request"),
    ]
    message = build_user_message("hello", result, recommendations=recommendations)
    assert "[BEST MATCH]" in message
    assert "[BEST VALUE]" in message


def test_build_user_message_without_recommendations_has_no_role_tags():
    result = RetrievalResult(products=[_product("a")])
    message = build_user_message("hello", result)
    assert "[BEST MATCH]" not in message


def test_build_clarifying_system_prompt_is_language_specific():
    en = build_clarifying_system_prompt("en")
    nl = build_clarifying_system_prompt("nl")
    assert "English" in en
    assert "Dutch" in nl
    assert "Don't recommend" in en  # never suggests a wine in this prompt
