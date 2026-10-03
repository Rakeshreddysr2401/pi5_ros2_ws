"""Piper provider: registered, degrades with ProviderUnavailable (never crashes
the node, which then falls back to Kokoro), and synthesises with the real
voice when it is installed."""
import os

import numpy as np
import pytest

from pi5_voice_pkg.tts_providers import REGISTRY, ProviderUnavailable
from pi5_voice_pkg.tts_providers.local_piper import LocalPiperProvider

VOICE = os.path.expanduser("~/ros2_ws/src/langrobo_ros/models/piper/en_US-lessac-medium.onnx")


def test_registered():
    assert REGISTRY["piper"] is LocalPiperProvider


def test_no_model_configured_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        LocalPiperProvider.from_config({"piper_model": ""}, {})


def test_missing_voice_file_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        LocalPiperProvider.from_config({"piper_model": "/nonexistent/voice.onnx"}, {})


@pytest.mark.skipif(not os.path.exists(VOICE), reason="piper voice not downloaded")
def test_real_voice_speaks_and_speed_shortens():
    normal = LocalPiperProvider.from_config({"piper_model": VOICE, "speed": 1.0}, {})
    a, sr = normal.synthesize("Hello, I am Mitra.")
    assert a.dtype == np.float32 and sr > 0 and len(a) / sr > 0.5 and np.abs(a).max() <= 1.0
    fast = LocalPiperProvider.from_config({"piper_model": VOICE, "speed": 1.3}, {})
    b, _ = fast.synthesize("Hello, I am Mitra.")
    assert len(b) < len(a)
