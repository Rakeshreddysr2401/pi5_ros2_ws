"""Name-gating: who is the person actually talking to?"""

import pytest

from pi5_voice_pkg.addressing import strip_alias

ALIASES = ["rakhi", "chotu", "hey pi"]


@pytest.mark.parametrize("text,expected", [
    ("rakhi what is the time", "what is the time"),
    ("Rakhi, what is the time?", "what is the time"),          # capitalised + punctuation
    ("hey pi turn on the light", "turn on the light"),          # multi-word alias
    ("can you rakhi go to the kitchen", "can you go to the kitchen"),  # name mid-sentence
    ("chotu!", ""),                                             # bare name = attention call
    ("Rakhi?", ""),
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
    assert strip_alias("rakhi hello", []) is None
    assert strip_alias("rakhi hello", ["", "  "]) is None


def test_first_matching_alias_wins():
    assert strip_alias("rakhi and chotu", ALIASES) == "and chotu"
