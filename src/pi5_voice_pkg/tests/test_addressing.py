"""Name-gating: who is the person actually talking to?"""

import pytest

from pi5_voice_pkg.addressing import sounds_like, strip_alias, strip_sound_alike

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
    # what Sarvam returned (2026-10-04) for a name that is a Telugu word ("friend")
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


# Heard live on Parakeet, 2026-10-20 (logs/wake): the name, misspelt.
NAMES = ["kino", "keeno"]


@pytest.mark.parametrize("text,expected", [
    ("Ekina, please move back.", "please move back"),           # "Hey Kino" said fast
    ("Hey Kina, can you move little forward?", "can you move little forward"),
    ("Hey Kinoff, what's the time now?", "what's the time now"),
    ("Hey Kina,", ""),
    ("Akina, please move back.", "please move back"),
    ("Thank you now. Ekina, go forward.", "go forward"),         # room talk before it goes
    ("Chino, come here", "come here"),
])
def test_a_misspelt_name_that_is_called(text, expected):
    assert strip_sound_alike(text, NAMES) == expected


@pytest.mark.parametrize("text", [
    "Gino is coming over",              # first word, but not called
    "I bought chinos yesterday",        # not first
    "kind of hot today",
    "Hey, come here to me.",
    "the king is here",
    "Kinda, yes.",
])
def test_a_sound_alike_that_is_not_called_or_not_the_name(text):
    assert strip_sound_alike(text, NAMES) is None


@pytest.mark.parametrize("word", ["keno", "chino", "gino", "kina", "ekina", "kinoff", "qino", "keanu"])
def test_sounds_like_the_name(word):
    assert sounds_like(word, NAMES)


@pytest.mark.parametrize("word", ["kind", "king", "skin", "kinda", "keen", "akin", "kimo", "nino"])
def test_does_not_sound_like_the_name(word):
    assert not sounds_like(word, NAMES)


@pytest.mark.parametrize("text,expected", [
    ("Play Kino, go back", "go back"),                 # "Hey Kino" through the AM-C28 (Apple)
    ("Play Kino Kaniye to me", "Kaniye to me"),
    ("Pay kino turn left", "turn left"),
    ("Hey, hey Kino, stop", "stop"),
    ("Play some music, Kino", "Play some music"),      # a real request before the name stays
])
def test_a_misheard_hey_before_the_name_is_dropped(text, expected):
    assert strip_alias(text, ALIASES) == expected
