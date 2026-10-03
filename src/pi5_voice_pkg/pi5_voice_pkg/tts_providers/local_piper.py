"""Piper on CPU — fast local TTS.

Measured on the Pi5, 2026-10-03, en_US-lessac-medium: 0.74 s for 3.05 s of
speech (RTF ~0.25) against Kokoro's 5.9 s for 2.9 s (RTF ~2.0) on the same
sentence. Kokoro sounds more natural; Piper starts talking ~8x sooner, which
is what a voice turn is judged on. Kokoro stays the fallback (tts_node), so a
missing or broken Piper voice degrades to Kokoro, never to silence.

Voices: python3 -m piper.download_voices <name> in models/piper/ (an .onnx and
its .onnx.json side by side). See docs/voice/PI5_VOICE.md.
"""

from collections.abc import Mapping

import numpy as np

from .base import ProviderUnavailable, TTSProvider


class LocalPiperProvider(TTSProvider):
    name = "piper"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "LocalPiperProvider":
        model = (params.get('piper_model') or '').strip()
        if not model:
            raise ProviderUnavailable('tts_piper_model is not set')
        return cls(model, float(params.get('speed', 1.0)))

    def __init__(self, model_path: str, speed: float = 1.0):
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as e:                       # not installed: fall back to Kokoro
            raise ProviderUnavailable(f'piper-tts not installed ({e})') from e
        try:
            self._voice = PiperVoice.load(model_path)
        except Exception as e:                         # missing / bad voice file
            raise ProviderUnavailable(f'cannot load piper voice {model_path!r} ({e})') from e
        # speed > 1 talks faster; piper's length_scale is the inverse
        self._config = SynthesisConfig(length_scale=1.0 / speed if speed > 0 else 1.0)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        try:
            chunks = list(self._voice.synthesize(text, syn_config=self._config))
        except Exception as e:
            raise ProviderUnavailable(f'piper synthesis failed ({e})') from e
        if not chunks:
            raise ProviderUnavailable('piper returned no audio')
        samples = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        return samples, chunks[0].sample_rate
