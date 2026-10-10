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
