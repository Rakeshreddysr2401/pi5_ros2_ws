"""The cue must answer a pause and keep quiet through a continued sentence."""

from pi5_voice_pkg.wake_cue import CueGate


def test_a_pause_after_the_name_gets_the_cue():
    gate = CueGate(delay_s=0.4)
    gate.on_wake(now=10.0)
    assert gate.update(voiced=False, now=10.1) is False    # still waiting
    assert gate.update(voiced=False, now=10.45) is True    # pause held -> speak


def test_a_continued_sentence_is_never_talked_over():
    """'Mitra, go to the kitchen' — speech starts before the delay is up."""
    gate = CueGate(delay_s=0.4)
    gate.on_wake(now=10.0)
    assert gate.update(voiced=True, now=10.1) is False
    assert gate.update(voiced=False, now=11.0) is False    # and stays cancelled
    assert gate.armed is False


def test_it_fires_at_most_once_per_wake():
    gate = CueGate(delay_s=0.4)
    gate.on_wake(now=0.0)
    assert gate.update(voiced=False, now=1.0) is True
    assert gate.update(voiced=False, now=2.0) is False


def test_nothing_fires_without_a_wake():
    gate = CueGate(delay_s=0.4)
    assert gate.update(voiced=False, now=99.0) is False
    assert gate.armed is False


def test_cancel_drops_a_pending_cue():
    gate = CueGate(delay_s=0.4)
    gate.on_wake(now=0.0)
    gate.cancel()
    assert gate.update(voiced=False, now=1.0) is False


def test_disabled_never_arms():
    gate = CueGate(delay_s=0.4, enabled=False)
    gate.on_wake(now=0.0)
    assert gate.armed is False
    assert gate.update(voiced=False, now=1.0) is False


def test_zero_delay_answers_on_the_next_quiet_frame():
    gate = CueGate(delay_s=0.0)
    gate.on_wake(now=5.0)
    assert gate.update(voiced=False, now=5.0) is True
