"""Pi5 STT — faster-whisper (CPU, int8) on CPU, same /voice/* wire protocol as the Jetson's stt_node.

Measured on this Pi5, 2026-09-04: `base` model, int8, 4 threads -> RTF ~0.75
(faster than real time — int8 IS the fast path for whisper on this ARM CPU,
unlike Kokoro's ONNX ops, see tts_node.py). No trained wake-word model exists
yet for this project (see Jetson TODO: hey_rakhi training pending), so this
runs the same "transcribe everything, gate on a name in the transcript"
fallback mode voice_pipeline.md documents for when openWakeWord is unavailable
— not a new design, the existing documented fallback.

Filters match VOICE_QUALITY.md's already-validated fixes (verified live on
this Pi5: without them, weak/reverberant audio hallucinates "Thank you."):
condition_on_previous_text=False, no_speech_threshold, avg_logprob cutoff.
"""

import collections
import re

import numpy as np
import rclpy
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel
from rclpy.node import Node
from std_msgs.msg import String

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
PRE_PAD_FRAMES = 10       # ~300ms of audio kept before speech is confirmed
END_SILENCE_FRAMES = 20   # ~600ms of silence ends the utterance
MIN_UTTERANCE_FRAMES = 10  # ~300ms — drop blips shorter than this

NO_SPEECH_THRESHOLD = 0.6
AVG_LOGPROB_MIN = -1.0


class STTNode(Node):
    def __init__(self):
        super().__init__('pi5_stt_node')
        self.declare_parameter('input_device', 'Blackwire')
        self.declare_parameter('model_size', 'base')
        self.declare_parameter('model_dir', '')
        self.declare_parameter('threads', 4)
        self.declare_parameter('vad_aggressiveness', 2)
        self.declare_parameter('wake_aliases', ['rakhi', 'chotu', 'hey pi'])
        self.declare_parameter('stop_words', ['stop'])

        device_hint = self.get_parameter('input_device').value
        model_size = self.get_parameter('model_size').value
        model_dir = self.get_parameter('model_dir').value
        threads = int(self.get_parameter('threads').value)
        self._vad = webrtcvad.Vad(int(self.get_parameter('vad_aggressiveness').value))
        self._aliases = [a.lower() for a in self.get_parameter('wake_aliases').value]
        self._stop_words = [w.lower() for w in self.get_parameter('stop_words').value]

        self._in_device = self._find_device(device_hint)
        self.get_logger().info(f'input device: {self._in_device}')

        self.get_logger().info(f'loading whisper {model_size} (int8, {threads} threads)...')
        self._model = WhisperModel(
            model_size, device='cpu', compute_type='int8',
            cpu_threads=threads, download_root=model_dir or None,
        )
        self.get_logger().info('whisper model loaded')

        self._input_pub = self.create_publisher(String, '/voice/user_input', 10)
        self._tts_stop_pub = self.create_publisher(String, '/voice/tts_stop', 10)

        self._ring: collections.deque = collections.deque(maxlen=PRE_PAD_FRAMES)
        self._utterance: list[bytes] = []
        self._in_speech = False
        self._silence_run = 0

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype='int16',
            blocksize=FRAME_SAMPLES, device=self._in_device,
            callback=self._on_audio,
        )
        self._stream.start()

    def _find_device(self, hint: str):
        for i, d in enumerate(sd.query_devices()):
            if hint.lower() in d['name'].lower() and d['max_input_channels'] > 0:
                return i
        self.get_logger().warning(f'no input device matching {hint!r}; using system default')
        return None

    def _on_audio(self, indata, frames, time_info, status):
        if status:
            self.get_logger().debug(f'input status: {status}')
        frame = indata[:, 0].tobytes()
        voiced = self._vad.is_speech(frame, SAMPLE_RATE)

        if not self._in_speech:
            self._ring.append(frame)
            if voiced:
                self._in_speech = True
                self._utterance = list(self._ring)
                self._silence_run = 0
            return

        self._utterance.append(frame)
        if voiced:
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= END_SILENCE_FRAMES:
                self._end_utterance()

    def _end_utterance(self):
        frames, self._utterance = self._utterance, []
        self._in_speech = False
        self._silence_run = 0
        self._ring.clear()
        if len(frames) < MIN_UTTERANCE_FRAMES:
            return
        pcm = np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        self._transcribe(pcm)

    def _transcribe(self, pcm: np.ndarray):
        segments, info = self._model.transcribe(
            pcm, language='en', condition_on_previous_text=False,
        )
        text_parts = []
        for s in segments:
            if s.no_speech_prob > NO_SPEECH_THRESHOLD and s.avg_logprob < AVG_LOGPROB_MIN:
                continue
            text_parts.append(s.text)
        text = ' '.join(text_parts).strip()
        if not text:
            return

        low = text.lower()
        if any(re.search(rf'\b{re.escape(w)}\b', low) for w in self._stop_words):
            self.get_logger().info(f'stop word heard: {text!r}')
            self._tts_stop_pub.publish(String(data='[stop]'))
            return

        for alias in self._aliases:
            idx = low.find(alias)
            if idx == -1:
                continue
            remainder = (text[:idx] + text[idx + len(alias):]).strip(' ,.!?')
            if not remainder:
                continue
            self.get_logger().info(f'addressed to me: {remainder!r}')
            self._input_pub.publish(String(data=remainder))
            return

        self.get_logger().debug(f'not addressed to me — ignored: {text!r}')


def main():
    rclpy.init()
    node = STTNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
