"""Name-gating: who is the person actually talking to?"""

import pytest

from pi5_voice_pkg.addressing import strip_alias

ALIASES = ["mitra", "hey mitra"]


@pytest.mark.parametrize("text,expected", [
    ("mitra what is the time", "what is the time"),
    ("Mitra, what is the time?", "what is the time"),          # capitalised + punctuation
    ("hey mitra turn on the light", "turn on the light"),       # multi-word alias, longest wins
    ("can you mitra go to the kitchen", "can you go to the kitchen"),  # name mid-sentence
    ("hey mitra!", ""),                                             # bare name = attention call
    ("Mitra?", ""),
])
def test_addressed_utterances(text, expected):
    assert strip_alias(text, ALIASES) == expected


@pytest.mark.parametrize("text", [
    "what is the time",
    "turn on the light",
    "he said he would tell me",
])
def test_unaddressed_utterances_return_none(text):
    assert strip_alias(text, ALIASES) is None


def test_alias_inside_another_word_does_not_count():
    """Substring matching fired on these; word boundaries do not."""
    assert strip_alias("the rakhis are ready", ALIASES) is None
    assert strip_alias("we bought rakhis today", ALIASES) is None


def test_empty_and_missing_inputs():
    assert strip_alias("", ALIASES) is None
    assert strip_alias(None, ALIASES) is None
    assert strip_alias("mitra hello", []) is None
    assert strip_alias("mitra hello", ["", "  "]) is None


def test_first_matching_alias_wins():
    assert strip_alias("mitra and mitra", ALIASES) == "and mitra"
