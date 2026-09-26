"""What reaches the speaker, and where sentences are cut.

Both halves of this are contracts the model is ASKED to honour in
SPEECH_STYLE and therefore cannot be trusted to: CLAUDE.md's own rule is
that a prompt instruction is a thing to measure. These tests are the
measurement.
"""

import pytest

from langrobo_core.utils.speech_stream import clean_for_speech, split_sentences


# ── clean_for_speech: the voice must never read markup aloud ────────────────

@pytest.mark.parametrize("raw,spoken", [
    ("**Ready** to go.", "Ready to go."),
    ("__Ready__ now", "Ready now"),
    ("*emphasis* only", "emphasis only"),
    ("## Heading", "Heading"),
    ("### A deeper heading", "A deeper heading"),
    ("- first item", "first item"),
    ("* starred item", "starred item"),
    ("1. numbered item", "numbered item"),
    ("2) also numbered", "also numbered"),
    ("> quoted line", "quoted line"),
    ("See `run.sh` now", "See run.sh now"),
    ("[the docs](http://x) explain", "the docs explain"),
    ("![a photo](file.jpg)", "a photo"),
    ("Look: https://example.com/a", "Look: a link"),
    ("go to www.example.com now", "go to a link now"),
    ("| a | b |", "a b"),
])
def test_markup_is_stripped_but_the_words_stay(raw, spoken):
    assert clean_for_speech(raw) == spoken


def test_emoji_go_but_other_scripts_stay():
    """The robot speaks Telugu — stripping 'non-ASCII' would eat its replies."""
    assert clean_for_speech("Done ✅ 🎉") == "Done"
    assert clean_for_speech("చెప్పండి బాస్ **now**") == "చెప్పండి బాస్ now"
    assert clean_for_speech("దొరికింది 🎉 బాస్") == "దొరికింది బాస్"


def test_markers_inside_words_are_left_alone():
    assert clean_for_speech("snake_case stays") == "snake_case stays"
    assert clean_for_speech("file_name_here.txt") == "file_name_here.txt"


def test_a_stray_marker_between_spaces_is_dropped_on_purpose():
    """"2 * 3" loses the asterisk. Deliberate: a lone marker surrounded by
    spaces is far more often orphaned markdown than arithmetic, and the
    prompt already asks for "two times three" in speech anyway."""
    assert clean_for_speech("2 * 3 is 6") == "2 3 is 6"


def test_a_chunk_of_pure_markup_becomes_nothing_to_say():
    """The caller skips these rather than publishing an empty utterance."""
    assert clean_for_speech("**") == ""
    assert clean_for_speech("```python") == ""
    assert clean_for_speech("   ") == ""
    assert clean_for_speech("") == ""


def test_multi_line_markdown_reply_reads_as_plain_speech():
    reply = ("## Status\n"
             "- battery is **full**\n"
             "- the `nav` stack is up\n"
             "See https://wiki/robot for more 🎉\n")
    assert clean_for_speech(reply) == (
        "Status battery is full the nav stack is up See a link for more")


def test_cleaning_is_idempotent():
    once = clean_for_speech("**Ready** — see `x`")
    assert clean_for_speech(once) == once


# ── split_sentences: where the voice takes a breath ────────────────────────

def test_complete_sentences_go_and_the_tail_waits():
    ready, rest = split_sentences("Hello there. How are you doing today? I am fi")
    assert ready == ["Hello there.", "How are you doing today?"]
    assert rest == "I am fi"


def test_an_abbreviation_is_not_the_end_of_a_sentence():
    ready, rest = split_sentences("I saw Dr. Rao in the kitchen. ")
    assert ready == ["I saw Dr. Rao in the kitchen."]
    assert rest == ""


def test_an_initial_is_not_the_end_of_a_sentence():
    ready, _ = split_sentences("It belongs to J. Smith upstairs. ")
    assert ready == ["It belongs to J. Smith upstairs."]


@pytest.mark.parametrize("text", [
    "The bottle is 1.45 metres away and I can reach it. ",
    "I will be there at 10.30 in the morning, boss. ",
    "It is about 32.5 percent charged right now. ",
])
def test_a_decimal_point_does_not_split_a_sentence(text):
    """"1.45 metres" must not be spoken as two utterances."""
    ready, rest = split_sentences(text)
    assert ready == [text.strip()], f"split inside a number: {ready}"


def test_a_short_fragment_waits_for_the_next_sentence():
    """"1." or "Hi." alone would be a whole TTS call for two syllables."""
    ready, rest = split_sentences("Hi. ")
    assert ready == []
    assert rest == "Hi. "


def test_a_newline_ends_a_chunk_even_without_punctuation():
    ready, _ = split_sentences("battery is full\nthe nav stack is up\n")
    assert ready == ["battery is full", "the nav stack is up"]


def test_an_endless_ramble_still_starts_speaking():
    """No punctuation for 250 chars: cut at a word boundary, not mid-word."""
    ready, rest = split_sentences("word " * 80)
    assert ready and not ready[0].endswith("wor")
    assert len(ready[0]) <= 250


def test_trailing_quotes_and_brackets_stay_with_their_sentence():
    ready, _ = split_sentences('She said "it is done." Then she left. ')
    assert ready[0] == 'She said "it is done."'
