"""utils/stop_words.says_stop: during "follow me", only these halt the wheels."""
import pytest

from langrobo_core.utils.stop_words import keeps_moving, says_stop


@pytest.mark.parametrize("text", [
    "stop", "Stop.", "Mitra, stop", "mitra stop following", "wait", "Mitra wait here",
    "stay there", "okay that's enough", "pause", "halt!", "freeze",
    "don't follow me", "Do not follow me anymore", "leave me now",
    "ruko", "bas", "रुको", "बस", "aagu", "aapandi", "chaalu", "ఆగు", "ఆగండి", "ఆపండి", "చాలు",
    "no, stop", "STOP STOP",
    # urgent words while it drives (2026-10-10: the gate now covers every drive)
    "careful!", "watch out", "look out Mitra", "that's wrong", "not there", "no", "no no no",
    "Mitra no", "nahi", "వద్దు",
])
def test_stop_words_stop(text):
    assert says_stop(text), text


@pytest.mark.parametrize("text", [
    # the transcript that ended the first real follow (2026-10-10)
    "Friend.",
    "", "   ", "Mitra, follow me", "come on, this way", "what time is it?",
    "Chikku, tell me about the therapy stick.", "faster please", "turn left here",
    "don't stop", "never stop following me", "do not stop",
    "nice job Mitra", "the bus is late", "stopwatch", "waiting room", "stays", "basket",
    # the transcript that killed a drive to the dining table (2026-10-10)
    "Akulam.", "no problem", "hey Mitra", "Mitra", "nobody is home",
])
def test_everyday_talk_does_not_stop(text):
    assert not says_stop(text), text


def test_unsure_errs_toward_stopping():
    # "can't" is not a known negation: when unsure, the robot stops -- the safe
    # side of this gate (MANUAL and "stop" always work; talk only costs a stop)
    assert says_stop("you can't stop now")


def test_curly_apostrophe_negation():
    assert not says_stop("don’t stop")
    assert says_stop("don’t follow me")


def test_keeps_moving_only_while_moving():
    assert keeps_moving(True, "Akulam.")
    assert keeps_moving(True, "what is that over there?")
    assert not keeps_moving(True, "Mitra, stop")
    assert not keeps_moving(True, "careful")
    # still: every utterance halts, as before this gate existed
    assert not keeps_moving(False, "Akulam.")
    assert not keeps_moving(False, "what time is it")
