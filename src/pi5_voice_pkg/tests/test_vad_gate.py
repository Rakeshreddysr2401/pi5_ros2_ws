"""The gate between the VAD and the cloud recogniser. No audio, no network."""

import pytest

from pi5_voice_pkg.vad_gate import GateConfig, evaluate

CFG = GateConfig()


def test_real_speech_passes():
    # 2s of speech, loud, mostly voiced
    assert evaluate(frames=66, rms=0.08, voiced_frames=55, cfg=CFG)


def test_a_blip_is_too_short():
    r = evaluate(frames=4, rms=0.5, voiced_frames=4, cfg=CFG)
    assert not r and "too short" in r.reason


def test_room_noise_is_too_quiet():
    r = evaluate(frames=60, rms=0.004, voiced_frames=40, cfg=CFG)
    assert not r and "too quiet" in r.reason


def test_a_single_noise_burst_is_mostly_silence():
    """One door slam trips the VAD, then 2s of nothing — the shape of the
    segments that produced invented transcripts."""
    r = evaluate(frames=70, rms=0.05, voiced_frames=12, cfg=CFG)
    assert not r and "mostly silence" in r.reason


def test_reason_is_populated_even_when_kept():
    r = evaluate(frames=66, rms=0.08, voiced_frames=55, cfg=CFG)
    assert r.keep and "rms" in r.reason


def test_thresholds_are_configurable():
    quiet = evaluate(frames=66, rms=0.005, voiced_frames=55, cfg=CFG)
    assert not quiet
    lenient = evaluate(frames=66, rms=0.005, voiced_frames=55,
                       cfg=GateConfig(min_rms=0.001))
    assert lenient


def test_zero_frames_never_divides_by_zero():
    assert not evaluate(frames=0, rms=0.0, voiced_frames=0, cfg=CFG)


@pytest.mark.parametrize("ratio_frames,expected", [(66, True), (23, False)])
def test_voiced_ratio_boundary(ratio_frames, expected):
    assert bool(evaluate(frames=66, rms=0.08, voiced_frames=ratio_frames, cfg=CFG)) is expected
