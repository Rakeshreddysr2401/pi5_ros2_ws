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


# ── What the listener has already heard ────────────────────────────────────

from uuid import uuid4                                        # noqa: E402

from langrobo_core.utils.speech_stream import SpeechStreamHandler  # noqa: E402


def _stream(handler, run_id, *tokens, end=True):
    handler.on_chat_model_start(None, None, run_id=run_id)
    for t in tokens:
        handler.on_llm_new_token(t, run_id=run_id)
    if end:
        handler.on_llm_end(None, run_id=run_id)


def test_a_fully_streamed_reply_needs_nothing_respoken():
    sent = []
    h = SpeechStreamHandler(sent.append)
    _stream(h, uuid4(), "The battery is full. ", "Everything looks fine. ")
    assert sent == ["The battery is full.", "Everything looks fine."]
    assert h.unspoken("The battery is full. Everything looks fine.") == ""


def test_only_the_tail_is_respoken_after_a_failed_partial_stream():
    """The real double-speak bug: attempt 1 streamed two sentences and then
    errored (so its text is dropped), the fallback returned the full reply.
    Publishing that whole reply said the opening twice."""
    sent = []
    h = SpeechStreamHandler(sent.append)
    run = uuid4()
    h.on_chat_model_start(None, None, run_id=run)
    h.on_llm_new_token("The battery is full. ", run_id=run)
    h.on_llm_error(RuntimeError("connection reset"), run_id=run)
    assert sent == ["The battery is full."]
    assert h.spoke("The battery is full. It is charging now.") is False
    assert h.unspoken("The battery is full. It is charging now.") == "It is charging now."


def test_an_unrelated_final_text_is_spoken_in_full():
    """A degraded message ("I'm having trouble...") shares no prefix with the
    partial — the listener must hear all of it."""
    sent = []
    h = SpeechStreamHandler(sent.append)
    run = uuid4()
    h.on_chat_model_start(None, None, run_id=run)
    h.on_llm_new_token("Let me check the battery. ", run_id=run)
    h.on_llm_error(RuntimeError("boom"), run_id=run)
    degraded = "I'm having trouble right now. Please try again in a moment."
    assert h.unspoken(degraded) == degraded


def test_whitespace_differences_do_not_count_as_unspoken():
    sent = []
    h = SpeechStreamHandler(sent.append)
    _stream(h, uuid4(), "All done, boss. ")
    assert h.unspoken("All   done,\nboss.") == ""


def test_nothing_streamed_means_the_whole_text_is_unspoken():
    h = SpeechStreamHandler(lambda t: None)
    assert h.unspoken("Hello there, boss.") == "Hello there, boss."
    assert h.unspoken("") == ""


def test_a_pre_tool_ack_then_the_answer_only_speaks_the_answer():
    """"Let me check." is streamed as the tool runs; the answer follows in a
    second LLM run. The listener has heard both — nothing to repeat."""
    sent = []
    h = SpeechStreamHandler(sent.append)
    _stream(h, uuid4(), "Let me check that for you. ")
    _stream(h, uuid4(), "The kitchen light is on. ")
    assert h.unspoken("The kitchen light is on.") == ""
