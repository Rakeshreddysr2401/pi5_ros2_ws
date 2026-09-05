"""Endpointing: which transcripts read as unfinished. No ROS, no audio."""

import pytest

from langrobo_core.utils.utterance import join_utterances, looks_incomplete


@pytest.mark.parametrize("text", [
    "rakhi I want you to",          # dangling verb
    "go to the kitchen and",        # dangling conjunction
    "put it on the",                # dangling determiner
    "I think we should go to",      # dangling preposition
    "can you umm",                  # filler
])
def test_dangling_utterances_are_held(text):
    assert looks_incomplete(text) is True


@pytest.mark.parametrize("text", [
    "go to the kitchen",                        # complete, no punctuation
    "Hey, shall we go to the movie tomorrow?",  # punctuated question
    "Stop.",                                    # punctuated command
    "tell me the colour of the sky",            # complete, ends on a noun
    "okay",                                     # short but finished
])
def test_finished_utterances_dispatch_immediately(text):
    assert looks_incomplete(text) is False


def test_terminal_punctuation_beats_a_dangling_word():
    """'…and.' is odd but punctuated — never hold a turn on it."""
    assert looks_incomplete("go to the kitchen and.") is False


def test_empty_is_not_incomplete():
    assert looks_incomplete("") is False
    assert looks_incomplete("   ") is False
    assert looks_incomplete(None) is False


def test_join_reads_as_one_sentence():
    assert join_utterances("rakhi I want you to ", " go to the kitchen") == \
        "rakhi I want you to go to the kitchen"
