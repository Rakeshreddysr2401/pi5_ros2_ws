"""faster-whisper on CPU. The always-available fallback — every other
provider falls back to this on failure, so this one must never raise
ProviderUnavailable itself (a transcribe() bug here has no fallback left).

Measured on the Pi5 (Cortex-A76, 4 threads), 2026-09-04: base/int8, RTF ~0.75.
Filters match VOICE_QUALITY.md's validated fix (see PI5_VOICE.md).
"""

import numpy as np
from faster_whisper import WhisperModel

from .base import STTProvider

NO_SPEECH_THRESHOLD = 0.6
AVG_LOGPROB_MIN = -1.0


class LocalWhisperProvider(STTProvider):
    name = "local"

    def __init__(self, model_size: str, model_dir: str, threads: int,
                 language: str = 'en', task: str = 'transcribe'):
        self._language = language
        self._task = task  # 'transcribe' or 'translate' (-> English); base model is weak at
        # translate — this is the degrade-to-local path when a cloud provider is down, not the
        # primary Telugu->English path (that's sarvam/soniox). See PI5_VOICE.md.
        self._model = WhisperModel(
            model_size, device='cpu', compute_type='int8',
            cpu_threads=threads, download_root=model_dir or None,
        )

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        segments, info = self._model.transcribe(
            pcm, language=self._language, task=self._task, condition_on_previous_text=False,
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > NO_SPEECH_THRESHOLD and s.avg_logprob < AVG_LOGPROB_MIN:
                continue
            parts.append(s.text)
        return ' '.join(parts).strip()
