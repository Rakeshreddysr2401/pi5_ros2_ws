import os
import pathlib

import numpy as np
import pytest

from pi5_voice_pkg.vad_silero import Hysteresis, SileroVad, VadUnavailable

MODEL = pathlib.Path(os.path.expanduser(
    '~/ros2_ws/src/langrobo_ros/models/vad/silero_vad.onnx'))


def test_hysteresis_starts_high_and_ends_low():
    h = Hysteresis(threshold=0.5, end_threshold=0.35)
    assert not h.update(0.4)          # below start: still silent
    assert h.update(0.6)              # crosses start
    assert h.update(0.4)              # a soft syllable does not end it
    assert not h.update(0.2)          # below end: over
    assert not h.update(0.4)          # and 0.4 is not enough to restart


def test_end_threshold_never_above_start():
    assert Hysteresis(0.3, 0.6).end_threshold == 0.3


def test_missing_model_is_unavailable_not_a_crash(tmp_path):
    with pytest.raises(VadUnavailable):
        SileroVad(str(tmp_path / 'nope.onnx'))


@pytest.mark.skipif(not MODEL.exists(), reason='silero model not downloaded (models/README.md)')
def test_silence_is_not_speech_and_buffers_across_frames():
    vad = SileroVad(str(MODEL))
    frame = np.zeros(480, np.int16).tobytes()      # 30 ms frames, model takes 512
    assert not any(vad.is_speech(frame, 16000) for _ in range(30))
    assert 0.0 <= vad.prob < 0.5


@pytest.mark.skipif(not MODEL.exists(), reason='silero model not downloaded (models/README.md)')
def test_long_listening_does_not_dull_it():
    """The drift found 2026-10-10: after minutes of feed the model under-scored a voice.
    Measured on this stand-in: fresh 0.76, two minutes in without the reset 0.21
    (missed), with it 0.95."""
    voice = (np.sin(2 * np.pi * 220 * np.arange(16000) / 16000) * 8000).astype(np.int16)

    def peak(vad):
        best = 0.0
        for i in range(0, len(voice) - 479, 480):
            vad.is_speech(voice[i:i + 480].tobytes(), 16000)
            best = max(best, vad.prob)
        return best

    def worn(idle_reset_s):
        rng = np.random.default_rng(0)
        vad = SileroVad(str(MODEL), idle_reset_s=idle_reset_s)
        for _ in range(4000):                              # ~2 minutes of room noise
            vad.is_speech(rng.normal(0, 300, 480).astype(np.int16).tobytes(), 16000)
        for _ in range(112):                               # then ~3 s of quiet
            vad.is_speech(np.zeros(480, np.int16).tobytes(), 16000)
        return peak(vad)

    with_reset, without = worn(2.0), worn(1e9)
    assert with_reset > 0.5                                # still heard after long listening
    assert with_reset > without + 0.3                      # and the reset is what does it
