"""local_whisper passes the decoding settings through, and stays on faster-whisper's
defaults when they are not given (no real model: WhisperModel is faked)."""
import types

import numpy as np

from pi5_voice_pkg.stt_providers import local_whisper as lw


class FakeModel:
    calls = []

    def __init__(self, *a, **k):
        pass

    def transcribe(self, pcm, **kw):
        FakeModel.calls.append(kw)
        seg = types.SimpleNamespace(text=" Mitra, what time is it?", no_speech_prob=0.0, avg_logprob=-0.2)
        return iter([seg]), None


def _provider(monkeypatch, params):
    monkeypatch.setattr(lw, "WhisperModel", FakeModel)
    FakeModel.calls = []
    base = {"model_size": "tiny.en", "model_dir": "", "threads": 2}
    return lw.LocalWhisperProvider.from_config({**base, **params}, {})


def test_beam_and_hotwords_passed(monkeypatch):
    p = _provider(monkeypatch, {"beam_size": 1, "hotwords": "Mitra"})
    assert p.transcribe(np.zeros(16000, dtype=np.float32), 16000) == "Mitra, what time is it?"
    assert FakeModel.calls[-1]["beam_size"] == 1
    assert FakeModel.calls[-1]["hotwords"] == "Mitra"


def test_defaults_unchanged(monkeypatch):
    p = _provider(monkeypatch, {})
    p.transcribe(np.zeros(16000, dtype=np.float32), 16000)
    assert FakeModel.calls[-1]["beam_size"] == 5          # faster-whisper's own default
    assert FakeModel.calls[-1]["hotwords"] is None


def test_empty_hotwords_is_none(monkeypatch):
    p = _provider(monkeypatch, {"hotwords": "", "beam_size": "1"})
    p.transcribe(np.zeros(16000, dtype=np.float32), 16000)
    assert FakeModel.calls[-1]["hotwords"] is None and FakeModel.calls[-1]["beam_size"] == 1
