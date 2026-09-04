"""Pi5 STT — mic capture + VAD + wake gate, same /voice/* wire protocol as
the Jetson's stt_node. Transcription itself is delegated to a swappable
provider (stt_providers/) — local faster-whisper, or a cloud STT with
built-in Telugu->English translation (Sarvam, Soniox). See PI5_VOICE.md.

Switching: set `stt_provider` in voice_params.yaml (local | sarvam | soniox)
and restart the node. If the selected cloud provider fails for any reason —
missing API key, network down, bad response — this transparently falls back
to local_whisper for that utterance and logs a warning; it never goes
silent (same "missing keys degrade, never crash" rule agent_node follows,
CLAUDE.md #4).

No trained wake-word model exists yet for this project (Jetson TODO:
hey_rakhi training pending), so this runs the same "transcribe everything,
gate on a name in the transcript" fallback mode VOICE_PIPELINE.md documents
for when openWakeWord is unavailable — not a new design.
"""

import collections
import os
import re

import numpy as np
import rclpy
import sounddevice as sd
import webrtcvad
from dotenv import load_dotenv
from rclpy.node import Node
from std_msgs.msg import String

from .stt_providers import REGISTRY, ProviderUnavailable
from .stt_providers.local_whisper import LocalWhisperProvider

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
PRE_PAD_FRAMES = 10       # ~300ms of audio kept before speech is confirmed
END_SILENCE_FRAMES = 20   # ~600ms of silence ends the utterance
MIN_UTTERANCE_FRAMES = 10  # ~300ms — drop blips shorter than this


class STTNode(Node):
    def __init__(self):
        super().__init__('pi5_stt_node')
        load_dotenv(os.path.expanduser(os.getenv('LANGROBO_ENV_FILE', '~/ros2_ws/.env')))

        self.declare_parameter('input_device', 'Blackwire')
        self.declare_parameter('model_size', 'base')
        self.declare_parameter('model_dir', '')
        self.declare_parameter('threads', 4)
        self.declare_parameter('vad_aggressiveness', 2)
        self.declare_parameter('wake_aliases', ['rakhi', 'chotu', 'hey pi'])
        self.declare_parameter('stop_words', ['stop'])
        self.declare_parameter('stt_provider', 'local')       # local | sarvam | soniox
        self.declare_parameter('stt_source_language', 'te')   # Telugu source for cloud translate
        self.declare_parameter('stt_target_language', 'en')

        device_hint = self.get_parameter('input_device').value
        model_size = self.get_parameter('model_size').value
        model_dir = self.get_parameter('model_dir').value
        threads = int(self.get_parameter('threads').value)
        self._vad = webrtcvad.Vad(int(self.get_parameter('vad_aggressiveness').value))
        self._aliases = [a.lower() for a in self.get_parameter('wake_aliases').value]
        self._stop_words = [w.lower() for w in self.get_parameter('stop_words').value]
        provider_name = self.get_parameter('stt_provider').value
        src_lang = self.get_parameter('stt_source_language').value
        tgt_lang = self.get_parameter('stt_target_language').value

        self._in_device = self._find_device(device_hint)
        self.get_logger().info(f'input device: {self._in_device}')

        self.get_logger().info(f'loading local whisper {model_size} (int8, {threads} threads)...')
        # Fallback path when a cloud provider fails: same task/language intent as the primary
        # provider, so "degraded" still means "still tries to answer the same question".
        fallback_task = 'translate' if provider_name != 'local' and tgt_lang == 'en' and src_lang != 'en' else 'transcribe'
        self._fallback = LocalWhisperProvider(
            model_size, model_dir, threads,
            language=src_lang if fallback_task == 'translate' else 'en', task=fallback_task,
        )
        self.get_logger().info('local whisper loaded (fallback path)')

        self._provider = self._fallback if provider_name == 'local' else self._build_provider(
            provider_name, src_lang, tgt_lang)
        self.get_logger().info(f'stt_provider = {self._provider.name}')

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

    def _build_provider(self, name: str, src_lang: str, tgt_lang: str):
        cls = REGISTRY.get(name)
        if cls is None:
            self.get_logger().error(f'unknown stt_provider {name!r}; using local')
            return self._fallback
        try:
            if name == 'sarvam':
                return cls(api_key=os.environ.get('SARVAM_API_KEY', ''),
                            source_language=f'{src_lang}-IN')
            if name == 'soniox':
                return cls(api_key=os.environ.get('SONIOX_API_KEY', ''),
                            source_language=src_lang, target_language=tgt_lang)
            return cls()
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{name} unavailable at startup ({e}); using local')
            return self._fallback

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
        try:
            text = self._provider.transcribe(pcm, SAMPLE_RATE)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{self._provider.name} failed ({e}); falling back to local')
            text = self._fallback.transcribe(pcm, SAMPLE_RATE)
        text = text.strip()
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
