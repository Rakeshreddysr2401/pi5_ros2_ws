"""openWakeWord detector — free, local, ONNX (reuses the onnxruntime already
installed for Kokoro TTS). Measured on this Pi5 2026-09-04: RTF ~0.2, model
loads in ~1s, so running it on every frame while asleep is negligible.

Ships pretrained models (alexa / hey_jarvis / hey_mycroft / hey_marvin /
timer / weather) — used as a stand-in until the custom "Rakhi" model is
trained (openWakeWord Colab, synthetic TTS data, ~1h -> rakhi.onnx). Swapping
to the custom word is a one-line config change (wake_model_path), no code.
"""

from collections.abc import Mapping

import numpy as np

from .base import WakeDetector, WakeUnavailable


class OpenWakeWordDetector(WakeDetector):
    name = "openwakeword"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "OpenWakeWordDetector":
        # wake_model_path (an explicit .onnx) wins; otherwise resolve a bundled
        # pretrained model by wake_word name (the stand-in until rakhi.onnx exists).
        return cls(
            model_path=params.get('wake_model_path', ''),
            wake_word=params.get('wake_word', 'hey_jarvis'),
            threshold=float(params.get('wake_threshold', 0.5)),
        )

    def __init__(self, model_path: str = '', wake_word: str = 'hey_jarvis', threshold: float = 0.5):
        try:
            import openwakeword
            from openwakeword.model import Model
        except ImportError as e:
            raise WakeUnavailable(f'openwakeword not installed: {e}') from e

        path = model_path
        if not path:
            matches = [p for p in openwakeword.get_pretrained_model_paths() if wake_word in p]
            if not matches:
                raise WakeUnavailable(
                    f'no pretrained model matches wake_word={wake_word!r} and no '
                    f'wake_model_path set')
            path = matches[0]

        try:
            self._model = Model(wakeword_model_paths=[path])
        except Exception as e:  # bad/missing file, onnx load failure, etc.
            raise WakeUnavailable(f'failed to load wake model {path!r}: {e}') from e

        self._key = list(self._model.models.keys())[0]
        self._threshold = threshold
        self._label = model_path or wake_word
        # openWakeWord expects ~80ms (1280-sample) chunks; stt_node feeds 30ms
        # (480-sample) frames, so buffer up to a full chunk before predicting.
        self._chunk = 1280
        self._buf = np.empty(0, dtype=np.int16)
        self.last_score = 0.0  # exposed for live threshold tuning (stt_node logs it)

    def process(self, frame: bytes) -> bool:
        self._buf = np.concatenate([self._buf, np.frombuffer(frame, dtype=np.int16)])
        fired = False
        while len(self._buf) >= self._chunk:
            chunk, self._buf = self._buf[:self._chunk], self._buf[self._chunk:]
            self.last_score = float(self._model.predict(chunk)[self._key])
            if self.last_score >= self._threshold:
                fired = True
        if fired:
            self.reset()  # clear buffers so the same audio can't fire twice
        return fired

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.int16)
        self._model.reset()
