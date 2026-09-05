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

Debug topics (added for live self-testing without SSH, 2026-09-04):
  /voice/debug_vad         — published the instant VAD hands off an
                              utterance, before transcription. If you speak
                              and see nothing here, the problem is VAD/mic,
                              not the STT provider.
  /voice/debug_transcript  — the raw transcribed text, before the wake-alias
                              gate, published for every utterance whether or
                              not it ends up addressed to the robot. Watch
                              this with `ros2 topic echo /voice/debug_transcript`.

Transcription runs on a background worker thread, not inside the
sounddevice audio callback. Found live 2026-09-04: a cloud provider's
request timeout (8s) blocked the callback thread for the full 8s, and the
mic never produced a usable utterance again afterward — PortAudio callbacks
that don't return promptly stall or corrupt the stream. The callback now
only does VAD + framing (fast, no I/O) and hands the finished utterance to
a queue; the worker thread does the (possibly slow) transcribe() call.
"""

import collections
import os
import queue
import re
import threading
import traceback

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
        # Seconds between [diag] lines. These ran unconditionally at ~1s, which
        # is ~86k INFO lines a day into journald on a robot meant to run 24/7.
        # 0 turns them off.
        self.declare_parameter('diag_log_period_s', 10.0)
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
        _diag_s = float(self.get_parameter('diag_log_period_s').value)
        self._diag_every = int(_diag_s * 1000 / FRAME_MS) if _diag_s > 0 else 0
        self._aliases = [a.lower() for a in self.get_parameter('wake_aliases').value]
        self._stop_words = [w.lower() for w in self.get_parameter('stop_words').value]
        provider_name = self.get_parameter('stt_provider').value
        src_lang = self.get_parameter('stt_source_language').value
        tgt_lang = self.get_parameter('stt_target_language').value

        self._in_device = self._find_device(device_hint)
        self.get_logger().info(f'input device: {self._in_device}')
        # [diag 2026-09-04] full enumeration — arecord and sounddevice number devices
        # from different backends, so log the whole table to catch index drift.
        self.get_logger().info('audio devices:\n' + '\n'.join(
            f'  [{i}] in={d["max_input_channels"]:>2} {d["name"]!r}'
            for i, d in enumerate(sd.query_devices())))

        self.get_logger().info(f'loading local whisper {model_size} (int8, {threads} threads)...')
        # One params dict handed to every provider's from_config(); each picks the keys it
        # needs. Adding a provider touches only stt_providers/ — never this node.
        base_params = {
            'model_size': model_size, 'model_dir': model_dir, 'threads': threads,
            'source_language': src_lang, 'target_language': tgt_lang,
        }
        # Fallback path when a cloud provider fails: same task/language intent as the primary
        # provider, so "degraded" still means "still tries to answer the same question".
        fallback_task = 'translate' if provider_name != 'local' and tgt_lang == 'en' and src_lang != 'en' else 'transcribe'
        self._fallback = LocalWhisperProvider.from_config(
            {**base_params, 'task': fallback_task,
             'language': src_lang if fallback_task == 'translate' else 'en'},
            os.environ,
        )
        self.get_logger().info('local whisper loaded (fallback path)')

        self._provider = self._fallback if provider_name == 'local' else self._build_provider(
            provider_name, base_params)
        self.get_logger().info(f'stt_provider = {self._provider.name}')

        self._input_pub = self.create_publisher(String, '/voice/user_input', 10)
        self._tts_stop_pub = self.create_publisher(String, '/voice/tts_stop', 10)
        self._debug_vad_pub = self.create_publisher(String, '/voice/debug_vad', 10)
        self._debug_transcript_pub = self.create_publisher(String, '/voice/debug_transcript', 10)

        self._ring: collections.deque = collections.deque(maxlen=PRE_PAD_FRAMES)
        self._utterance: list[bytes] = []
        self._in_speech = False
        self._silence_run = 0
        self._dbg_frames = 0  # [diag 2026-09-04] audio-callback heartbeat counter

        self._queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype='int16',
            blocksize=FRAME_SAMPLES, device=self._in_device,
            callback=self._on_audio,
        )
        self._stream.start()

    def _build_provider(self, name: str, params: dict):
        cls = REGISTRY.get(name)
        if cls is None:
            self.get_logger().error(f'unknown stt_provider {name!r}; using local')
            return self._fallback
        try:
            return cls.from_config(params, os.environ)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{name} unavailable at startup ({e}); using local')
            return self._fallback

    def _find_device(self, hint: str):
        # '' matches every name, so an unset hint used to take device 0 without
        # warning — wrong mic, no clue why.
        hint = (hint or '').strip()
        if not hint:
            self.get_logger().info('no input_device hint — using the system default')
            return None
        for i, d in enumerate(sd.query_devices()):
            if hint.lower() in d['name'].lower() and d['max_input_channels'] > 0:
                return i
        self.get_logger().warning(f'no input device matching {hint!r}; using system default')
        return None

    def _on_audio(self, indata, frames, time_info, status):
        if status:
            # [diag 2026-09-04] was .debug() — ALSA input-overflow was invisible at the
            # default level. If the callback is starved (e.g. whisper pegging all cores),
            # dropped frames show up here and VAD silently sees nothing.
            self.get_logger().warning(f'input status: {status}')
        frame = indata[:, 0].tobytes()
        voiced = self._vad.is_speech(frame, SAMPLE_RATE)
        # [diag 2026-09-04] ~1s heartbeat proving the callback fires and whether VAD ever
        # classifies voice. Remove once the VAD-silence issue is closed (see TODO.md).
        self._dbg_frames += 1
        if self._diag_every and self._dbg_frames % self._diag_every == 0:
            self.get_logger().info(
                f'[diag] frames={self._dbg_frames} voiced={voiced} in_speech={self._in_speech}')

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
        ms = len(frames) * FRAME_MS
        self._debug_vad_pub.publish(String(data=f'utterance detected: {len(frames)} frames (~{ms}ms)'))
        pcm = np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        self._queue.put(pcm)  # hand off — never block the audio callback (see module docstring)

    # Words that turn a stop word into something else entirely.
    _STOP_NEGATIONS = ('dont', "don't", 'do not', 'never', 'without', 'not')

    def _is_stop_command(self, low: str) -> bool:
        """True only for an utterance that IS a stop command.

        Substring matching fired on 'don\'t stop' and 'stop by the shop later' —
        a halt word must not be triggered by a sentence that merely contains it.
        A real stop is short and made of stop words, wake aliases and filler.
        """
        if any(neg in low for neg in self._STOP_NEGATIONS):
            return False
        words = re.findall(r"[\w']+", low)
        if not words or len(words) > 4:
            return False
        if not any(w in self._stop_words for w in words):
            return False
        filler = set(self._aliases) | {'please', 'now', 'just', 'hey', 'ok', 'okay'}
        return all(w in self._stop_words or w in filler for w in words)

    def _worker_loop(self):
        while rclpy.ok():
            pcm = self._queue.get()
            try:
                self._transcribe(pcm)
            except Exception:
                # rclpy's logger has no .exception() (stdlib logging does) — this call itself
                # used to raise AttributeError and kill the worker thread silently. Found live
                # 2026-09-04 in the TTS-side twin of this bug (tts_node.py _play).
                self.get_logger().error(f'transcribe worker failed\n{traceback.format_exc()}')

    def _transcribe(self, pcm: np.ndarray):
        try:
            text = self._provider.transcribe(pcm, SAMPLE_RATE)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{self._provider.name} failed ({e}); falling back to local')
            text = self._fallback.transcribe(pcm, SAMPLE_RATE)
        text = text.strip()
        self._debug_transcript_pub.publish(String(data=text if text else '(empty — filtered as noise/silence)'))
        if not text:
            return

        low = text.lower()
        if self._is_stop_command(low):
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
