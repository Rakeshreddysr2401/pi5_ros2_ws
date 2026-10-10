"""Silero VAD (v5) on plain onnxruntime — a drop-in for webrtcvad.Vad.

Why: webrtcvad answers "is this frame speech-LIKE", and on this robot that
meant nearly everything. Measured 2026-10-10 on the USB AM-C28 array: it
called 14-63% of an empty room "speech" (air rumble on the mic, the room),
so utterances ran to the 12 s cap -- the log showed one 12 s "utterance"
transcribed as 'Music' every ~12 s for days, and the live Sarvam test waited
~15 s for a sentence to end. Silero is a small neural model trained to tell
voices from noise and music: 0% of the same room, ~1.5 ms per 30 ms frame on
the Pi 5 (one thread).

Same call as webrtcvad — `is_speech(frame_bytes, sample_rate)` on 30 ms int16
frames — so stt_node's segmenter is unchanged. Silero wants 512-sample windows
(+ 64 samples of context), so frames are buffered and each call answers with
the newest window's verdict.

Hysteresis: speech STARTS above `threshold` and only ENDS below
`end_threshold`, so a soft syllable mid-sentence does not count as the pause
that ends the utterance.

Pure: no rclpy. The model file is fetched like every other voice weight
(models/README.md); missing model / onnxruntime raises VadUnavailable and the
node keeps webrtcvad (CLAUDE.md #5: degrade, never crash).
"""

import numpy as np

SAMPLE_RATE = 16000
WINDOW = 512        # samples per Silero call at 16 kHz
CONTEXT = 64        # samples of the previous window the v5 model expects in front


class VadUnavailable(Exception):
    """The model or onnxruntime could not be loaded."""


class Hysteresis:
    """Turn a probability stream into speech / not-speech with two thresholds."""

    def __init__(self, threshold: float = 0.5, end_threshold: float = 0.35):
        self.threshold = threshold
        self.end_threshold = min(end_threshold, threshold)
        self.active = False

    def update(self, prob: float) -> bool:
        if self.active:
            self.active = prob >= self.end_threshold
        else:
            self.active = prob >= self.threshold
        return self.active

    def reset(self) -> None:
        self.active = False


class SileroVad:
    def __init__(self, model_path: str, threshold: float = 0.5, end_threshold: float = 0.35):
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise VadUnavailable(f'onnxruntime not installed ({e})') from e
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1      # 1.5 ms a frame; keep the cores for STT/TTS
        opts.inter_op_num_threads = 1
        try:
            self._session = ort.InferenceSession(model_path, opts, providers=['CPUExecutionProvider'])
        except Exception as e:
            raise VadUnavailable(f'cannot load silero model {model_path!r} ({e})') from e
        names = {i.name for i in self._session.get_inputs()}
        if names != {'input', 'state', 'sr'}:
            raise VadUnavailable(f'{model_path!r} is not a Silero v5 model (inputs {sorted(names)})')
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._gate = Hysteresis(threshold, end_threshold)
        self.prob = 0.0
        self.reset()

    def reset(self) -> None:
        """Forget the stream (call when the mic was muted or reopened)."""
        self._state = np.zeros((2, 1, 128), np.float32)
        self._context = np.zeros(CONTEXT, np.float32)
        self._pending = np.zeros(0, np.float32)
        self._gate.reset()
        self.prob = 0.0

    def is_speech(self, frame: bytes, sample_rate: int = SAMPLE_RATE) -> bool:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f'silero VAD runs at {SAMPLE_RATE} Hz, got {sample_rate}')
        pcm = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, pcm])
        while len(self._pending) >= WINDOW:
            window, self._pending = self._pending[:WINDOW], self._pending[WINDOW:]
            x = np.concatenate([self._context, window])[None, :]
            out, self._state = self._session.run(
                None, {'input': x, 'state': self._state, 'sr': self._sr})
            self._context = window[-CONTEXT:]
            self.prob = float(out[0, 0])
            self._gate.update(self.prob)
        return self._gate.active
