"""Planner: routes and filter extraction, EN + NL."""

from __future__ import annotations

from schemas import ColorType

from chat.planner import plan


def test_color_and_max_price_en():
    p = plan("do you have a red wine under €15?")
    assert p.route == "catalog"
    assert p.filters.color_type is ColorType.red
    assert p.filters.max_price_cents == 1500


def test_color_and_max_price_nl():
    p = plan("een rode wijn tot 15 euro graag")
    assert p.filters.color_type is ColorType.red
    assert p.filters.max_price_cents == 1500


def test_decimal_comma_nl():
    p = plan("witte wijn onder de €12,50")
    assert p.filters.color_type is ColorType.white
    assert p.filters.max_price_cents == 1250


def test_min_price():
    p = plan("a special bottle over €30 for a gift")
    assert p.filters.min_price_cents == 3000
    assert p.filters.max_price_cents is None


def test_country_en_and_nl():
    assert plan("a french red").filters.country == "France"
    assert plan("heb je een spaanse wijn?").filters.country == "Spain"


def test_policy_route_en_and_nl():
    assert plan("what are your opening hours?").route == "policy"
    assert plan("wat zijn de verzendkosten?").route == "policy"


def test_no_filters_plain_query():
    p = plan("something nice with dinner")
    assert p.route == "catalog"
    assert not p.filters.any()


def test_wants_cheaper():
    assert plan("do you have a cheaper one?").wants_cheaper
    assert plan("heb je iets goedkopers? een goedkopere fles").wants_cheaper
    assert not plan("a nice red").wants_cheaper


def test_sparkling_synonyms():
    assert plan("a bottle of prosecco or cava").filters.color_type is ColorType.sparkling
    assert plan("mousserende wijn voor oud en nieuw").filters.color_type is ColorType.sparkling


def test_negation_does_not_lock_onto_first_color_word():
    p = plan("not red, I'd like a white wine please")
    assert p.filters.color_type is ColorType.white


def test_negation_nl():
    p = plan("geen rode wijn, liever een witte")
    assert p.filters.color_type is ColorType.white


def test_positive_mention_after_color_still_works():
    # sanity: negation handling must not break the existing "first match wins"
    # behaviour when there's no negation at all.
    p = plan("I want white, not red")
    assert p.filters.color_type is ColorType.white


def test_white_varietal_without_the_word_white():
    assert plan("do you have a chardonnay?").filters.color_type is ColorType.white
    assert plan("I'd like a nice riesling").filters.color_type is ColorType.white


def test_red_varietal_without_the_word_red():
    assert plan("a bottle of malbec please").filters.color_type is ColorType.red


def test_two_word_varietal_phrases():
    assert plan("something with sauvignon blanc notes").filters.color_type is ColorType.white
    assert plan("a pinot noir for dinner").filters.color_type is ColorType.red


def test_ambiguous_bare_varietal_is_not_guessed():
    # bare "pinot" spans multiple colours (noir/grigio/blanc) — must not guess.
    assert plan("something pinot, surprise me").filters.color_type is None
