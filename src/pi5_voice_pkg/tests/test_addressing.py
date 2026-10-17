"""Name-gating: who is the person actually talking to?"""

import pytest

from pi5_voice_pkg.addressing import strip_alias

ALIASES = ["kino", "hey kino"]


@pytest.mark.parametrize("text,expected", [
    ("kino what is the time", "what is the time"),
    ("Kino, what is the time?", "what is the time"),          # capitalised + punctuation
    ("hey kino turn on the light", "turn on the light"),       # multi-word alias, longest wins
    ("can you kino go to the kitchen", "can you go to the kitchen"),  # name mid-sentence
    ("hey kino!", ""),                                             # bare name = attention call
    ("Kino?", ""),
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
    assert strip_alias("kino hello", []) is None
    assert strip_alias("kino hello", ["", "  "]) is None


def test_first_matching_alias_wins():
    assert strip_alias("kino and kino", ALIASES) == "and kino"


from pi5_voice_pkg.addressing import strip_leading_alias  # noqa: E402

LEADING = ["friend", "friends"]


@pytest.mark.parametrize("text,expected", [
    # what Sarvam actually returned for "మిత్ర, ..." on 2026-10-04
    ("Friend, shall we go to a movie tomorrow?", "shall we go to a movie tomorrow"),
    ("Hey friend, play a song", "play a song"),
    ("Oh friend! What time is it?", "What time is it"),
    ("My friend, set a timer for five minutes.", "set a timer for five minutes"),
    ("Friend?", ""),
])
def test_translated_name_at_the_start(text, expected):
    assert strip_leading_alias(text, LEADING) == expected


@pytest.mark.parametrize("text", [
    "My friend is coming over tomorrow",        # "friend" + no pause, a statement
    "I called a friend yesterday",
    "Shall we go with friends?",
    "friendly people live here",
])
def test_friend_inside_a_sentence_is_not_the_name(text):
    assert strip_leading_alias(text, LEADING) is None


# ── earlier sentences in the same audio are not the request (2026-10-10) ──

def test_room_talk_before_the_name_is_dropped():
    from pi5_voice_pkg.addressing import strip_alias
    heard = "Small water cool. Pizza is ready. I know you like to play. Kino, follow me"
    assert strip_alias(heard, ["kino"]) == "follow me"
    assert strip_alias("Okay. Kino, stop.", ["kino"]) == "stop"
    assert strip_alias("Is it ready? Hey Kino, come here", ["hey kino", "kino"]) == "come here"


def test_a_name_inside_the_sentence_keeps_it():
    from pi5_voice_pkg.addressing import strip_alias
    assert strip_alias("Go to the kitchen, Kino.", ["kino"]) == "Go to the kitchen"
    assert strip_alias("What time is it Kino?", ["kino"]) == "What time is it"
    assert strip_alias("Kino, what time is it?", ["kino"]) == "what time is it"
    assert strip_alias("Kino", ["kino"]) == ""
    assert strip_alias("no name here", ["kino"]) is None
