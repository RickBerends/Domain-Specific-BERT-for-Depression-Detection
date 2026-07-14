"""EN/NL language detection heuristic."""

from __future__ import annotations

from chat.lang import detect_language


def test_detects_dutch():
    assert detect_language("Welke goedkope rode wijn hebben jullie?") == "nl"


def test_detects_english():
    assert detect_language("What cheap red wine do you have?") == "en"


def test_defaults_to_english_on_no_signal():
    assert detect_language("Chianti Classico 2021") == "en"


def test_empty_message_defaults_to_english():
    assert detect_language("") == "en"
