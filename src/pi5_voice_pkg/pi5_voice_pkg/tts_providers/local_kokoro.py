"""Kokoro-onnx on CPU. The always-available fallback — every other TTS
provider falls back to this on failure, so it must never raise
ProviderUnavailable itself.

Measured on the Pi5 (Cortex-A76, 4 threads), 2026-09-04: fp32, RTF ~1.8
(slower than real time). int8 quantized measured WORSE (RTF ~3.75) — ARM
NEON has no fast int8 path for this op set. Use fp32. See PI5_VOICE.md.
"""

from collections.abc import Mapping

import numpy as np
import onnxruntime as ort
from kokoro_onnx import Kokoro

from .base import TTSProvider


class LocalKokoroProvider(TTSProvider):
    name = "local"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "LocalKokoroProvider":
        return cls(params['model_path'], params['voices_path'], params['voice'],
                   params['speed'], params['threads'])

    def __init__(self, model_path: str, voices_path: str, voice: str, speed: float, threads: int):
        self._voice = voice
        self._speed = speed
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        sess = ort.InferenceSession(model_path, sess_options=so, providers=['CPUExecutionProvider'])
        self._kokoro = Kokoro.from_session(sess, voices_path)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        samples, sr = self._kokoro.create(text, voice=self._voice, speed=self._speed, lang='en-us')
        return samples, sr
