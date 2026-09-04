"""Pi5 TTS — queueing, playback and the /voice/* wire protocol, same as
before. Synthesis itself is delegated to a swappable provider
(tts_providers/) — local Kokoro, or a cloud TTS (Sarvam, Soniox). See
PI5_VOICE.md.

Switching: set `tts_provider` in voice_params.yaml (local | sarvam | soniox)
and restart the node. Same degrade rule as stt_node: any provider failure
(missing key, network, timeout, bad response) falls back to local_kokoro for
that sentence and logs a warning — never goes silent (CLAUDE.md #4).

Why add cloud TTS at all: local Kokoro fp32 measured RTF ~1.8 on this Pi5
(slower than real time). Both Sarvam and Soniox publish sub-second/
streaming latency for TTS specifically — this is a latency motivation, not
the Telugu-translation motivation that justified the STT providers.
"""

import os
import queue
import threading
import traceback

import numpy as np
import rclpy
import sounddevice as sd
from dotenv import load_dotenv
from rclpy.node import Node
from std_msgs.msg import Bool, String

from .tts_providers import REGISTRY, ProviderUnavailable
from .tts_providers.local_kokoro import LocalKokoroProvider

SPEECH_EOU = "<|eou|>"  # must match langrobo_core/utils/speech_stream.py
CHUNK_FRAMES_S = 0.1    # playback granularity for fast stop, in seconds of audio


class TTSNode(Node):
    def __init__(self):
        super().__init__('pi5_tts_node')
        load_dotenv(os.path.expanduser(os.getenv('LANGROBO_ENV_FILE', '~/ros2_ws/.env')))

        self.declare_parameter('model_path', '')
        self.declare_parameter('voices_path', '')
        self.declare_parameter('voice', 'af_heart')
        self.declare_parameter('speed', 1.0)
        self.declare_parameter('output_device', 'Blackwire')
        self.declare_parameter('threads', 4)
        self.declare_parameter('tts_provider', 'local')   # local | sarvam | sarvam_translate | soniox
        self.declare_parameter('tts_language', 'en')
        self.declare_parameter('tts_sarvam_voice', 'ritu')
        self.declare_parameter('tts_soniox_voice', 'Adrian')
        # Only used by tts_provider=sarvam_translate (English text -> Telugu speech).
        self.declare_parameter('tts_translate_from', 'en')
        self.declare_parameter('tts_translate_to', 'te')

        model_path = self.get_parameter('model_path').value
        voices_path = self.get_parameter('voices_path').value
        voice = self.get_parameter('voice').value
        speed = float(self.get_parameter('speed').value)
        device_hint = self.get_parameter('output_device').value
        threads = int(self.get_parameter('threads').value)
        provider_name = self.get_parameter('tts_provider').value
        language = self.get_parameter('tts_language').value

        self._out_device = self._find_device(device_hint)
        self.get_logger().info(f'output device: {self._out_device}')

        # One params dict handed to every provider's from_config(); each picks the keys it
        # needs. Adding a provider touches only tts_providers/ — never this node.
        params = {
            'model_path': model_path, 'voices_path': voices_path, 'voice': voice,
            'speed': speed, 'threads': threads, 'language': language,
            'sarvam_voice': self.get_parameter('tts_sarvam_voice').value,
            'soniox_voice': self.get_parameter('tts_soniox_voice').value,
            'translate_from': self.get_parameter('tts_translate_from').value,
            'translate_to': self.get_parameter('tts_translate_to').value,
        }
        self._fallback = LocalKokoroProvider.from_config(params, os.environ)
        self.get_logger().info('kokoro model loaded (fallback path)')

        self._provider = self._fallback if provider_name == 'local' else self._build_provider(
            provider_name, params)
        self.get_logger().info(f'tts_provider = {self._provider.name}')

        self._speaking_pub = self.create_publisher(Bool, '/voice/tts_speaking', 10)
        self.create_subscription(String, '/voice/robot_speech', self._on_speech, 10)
        self.create_subscription(String, '/voice/tts_stop', self._on_stop, 10)

        self._q: queue.Queue[str] = queue.Queue()
        self._interrupt = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _build_provider(self, name: str, params: dict):
        cls = REGISTRY.get(name)
        if cls is None:
            self.get_logger().error(f'unknown tts_provider {name!r}; using local')
            return self._fallback
        try:
            return cls.from_config(params, os.environ)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{name} unavailable at startup ({e}); using local')
            return self._fallback

    def _find_device(self, hint: str):
        for i, d in enumerate(sd.query_devices()):
            if hint.lower() in d['name'].lower() and d['max_output_channels'] > 0:
                return i
        self.get_logger().warning(f'no output device matching {hint!r}; using system default')
        return None

    def _on_speech(self, msg: String):
        self._q.put(msg.data)

    def _on_stop(self, msg: String):
        self._interrupt.set()
        with self._q.mutex:
            self._q.queue.clear()

    def _run(self):
        speaking = False
        while rclpy.ok():
            text = self._q.get()
            if text == SPEECH_EOU:
                if speaking:
                    self._speaking_pub.publish(Bool(data=False))
                    speaking = False
                continue
            if self._interrupt.is_set():
                self._interrupt.clear()
                continue
            if not speaking:
                self._speaking_pub.publish(Bool(data=True))
                speaking = True
            try:
                self._speak(text)
            except Exception:
                # Belt and suspenders on top of _speak's own try/excepts: an exception that
                # somehow still escapes must not kill this thread — every future _run() call
                # would silently do nothing forever. Found live 2026-09-04 (see _play's docstring).
                self.get_logger().error(f'unhandled error speaking {text!r}\n{traceback.format_exc()}')

    def _speak(self, text: str):
        try:
            samples, sr = self._provider.synthesize(text)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{self._provider.name} failed ({e}); falling back to local')
            try:
                samples, sr = self._fallback.synthesize(text)
            except Exception:
                # rclpy's logger has no .exception() (stdlib logging does) — format manually or
                # this masks the real error behind an AttributeError. Found live 2026-09-04.
                self.get_logger().error(f'local fallback synthesis also failed for: {text!r}\n{traceback.format_exc()}')
                return
        except Exception:
            self.get_logger().error(f'synthesis failed for: {text!r}\n{traceback.format_exc()}')
            return
        self._play(samples, sr)

    def _play(self, samples: np.ndarray, sr: int):
        chunk_frames = int(sr * CHUNK_FRAMES_S)
        try:
            with sd.OutputStream(samplerate=sr, channels=1, dtype='float32', device=self._out_device) as stream:
                for i in range(0, len(samples), chunk_frames):
                    if self._interrupt.is_set():
                        break
                    stream.write(samples[i:i + chunk_frames])
        except Exception:
            self.get_logger().error(f'playback failed\n{traceback.format_exc()}')


def main():
    rclpy.init()
    node = TTSNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
