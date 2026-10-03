"""faster-whisper on CPU. The always-available fallback — every other
provider falls back to this on failure, so this one must never raise
ProviderUnavailable itself (a transcribe() bug here has no fallback left).

Measured on the Pi5 (Cortex-A76, 4 threads), 2026-09-04: base/int8, RTF ~0.75.
Filters match docs/voice/VOICE_QUALITY.md's validated fix (see docs/voice/PI5_VOICE.md).
"""

from collections.abc import Mapping

import numpy as np
from faster_whisper import WhisperModel

from .base import STTProvider

NO_SPEECH_THRESHOLD = 0.6
AVG_LOGPROB_MIN = -1.0


class LocalWhisperProvider(STTProvider):
    name = "local"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "LocalWhisperProvider":
        # language/task are set by the node: 'en'/transcribe when local is the
        # chosen provider, or src-lang/translate when local is the safety-net
        # fallback behind a translating cloud provider (see stt_node).
        return cls(
            params['model_size'], params['model_dir'], params['threads'],
            language=params.get('language', 'en'), task=params.get('task', 'transcribe'),
            beam_size=int(params.get('beam_size', 5)), hotwords=params.get('hotwords') or None,
        )

    def __init__(self, model_size: str, model_dir: str, threads: int,
                 language: str = 'en', task: str = 'transcribe',
                 beam_size: int = 5, hotwords: str | None = None):
        self._language = language
        # beam_size 1 + hotwords, measured 2026-10-03 on Kokoro-spoken commands
        # degraded like the 8 kHz Bluetooth (HFP) mic: with base the name came
        # through 13/20 at beam 5 ("Metro", "Metra", "Misha", "Mittra") and
        # 20/20 with hotwords="Mitra" at beam 1, faster too; tiny.en the same
        # 20/20 at about half base's time. An initial_prompt instead made it
        # DROP the name (9/20). docs/voice/PI5_VOICE.md, "Local voice tuning".
        self._beam_size = beam_size
        self._hotwords = hotwords
        self._task = task  # 'transcribe' or 'translate' (-> English); base model is weak at
        # translate — this is the degrade-to-local path when a cloud provider is down, not the
        # primary Telugu->English path (that's sarvam/soniox). See docs/voice/PI5_VOICE.md.
        self._model = WhisperModel(
            model_size, device='cpu', compute_type='int8',
            cpu_threads=threads, download_root=model_dir or None,
        )

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        segments, info = self._model.transcribe(
            pcm, language=self._language, task=self._task, condition_on_previous_text=False,
            beam_size=self._beam_size, hotwords=self._hotwords,
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > NO_SPEECH_THRESHOLD and s.avg_logprob < AVG_LOGPROB_MIN:
                continue
            parts.append(s.text)
        return ' '.join(parts).strip()
