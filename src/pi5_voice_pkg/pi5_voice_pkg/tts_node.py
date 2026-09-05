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

import json
import os
import queue
import threading
import time
import traceback

import numpy as np
import rclpy
import sounddevice as sd
from dotenv import load_dotenv
from rclpy.node import Node
from std_msgs.msg import Bool, String

from . import bt_audio
from .tts_providers import REGISTRY, ProviderUnavailable
from .tts_providers.local_kokoro import LocalKokoroProvider

# How many synthesised sentences may wait ahead of the speaker.
PREFETCH_SENTENCES = 2

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
        # Bluetooth speaker (e.g. a Boat Stone). Empty = wired audio only.
        # a2dp = speaker only, full quality. hfp = the speaker's call mic too,
        # 8-16kHz mono — worse for both legs, so it is opt-in.
        self.declare_parameter('bt_mac', '')
        self.declare_parameter('bt_profile', 'a2dp')

        model_path = self.get_parameter('model_path').value
        voices_path = self.get_parameter('voices_path').value
        voice = self.get_parameter('voice').value
        speed = float(self.get_parameter('speed').value)
        device_hint = self.get_parameter('output_device').value
        threads = int(self.get_parameter('threads').value)
        provider_name = self.get_parameter('tts_provider').value
        language = self.get_parameter('tts_language').value

        # A Bluetooth speaker is a PipeWire node, not an ALSA card, so
        # sounddevice cannot address it directly: connect it, make it the
        # default sink, and play through the `pipewire` ALSA device. Idempotent
        # — stt_node runs the same call and neither depends on the other's
        # ordering. Failure just leaves the wired hint in place.
        bt_mac = self.get_parameter('bt_mac').value
        if bt_mac:
            bt = bt_audio.ensure(bt_mac, self.get_parameter('bt_profile').value)
            for note in bt['notes']:
                self.get_logger().info(f'bluetooth: {note}')
            if bt['sink']:
                device_hint = 'pipewire'
                self.get_logger().info(
                    f"bluetooth sink {bt['sink']!r} is default — output via pipewire")
            else:
                self.get_logger().warning(
                    f'bluetooth speaker unavailable; falling back to {device_hint!r}')

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
        # Per-sentence synthesis cost + which provider paid it. agent_node turns
        # these into LangSmith runs joined to the turn by trace_id, so the output
        # leg of a turn (Kokoro RTF ~1.8 vs a sub-second cloud call, or a silent
        # fallback to local) is visible next to the LLM that produced the text.
        self._tts_meta_pub = self.create_publisher(String, '/voice/tts_meta', 10)
        self.create_subscription(String, '/voice/robot_speech', self._on_speech, 10)
        self.create_subscription(String, '/voice/tts_stop', self._on_stop, 10)

        self._q: queue.Queue[str] = queue.Queue()
        # Synthesised audio waiting to be played. Bounded so synthesis stays a
        # sentence or two ahead — enough to cover the gap, not so far that a
        # barge-in has a backlog to throw away.
        self._audio_q: queue.Queue = queue.Queue(maxsize=PREFETCH_SENTENCES)
        self._interrupt = threading.Event()
        self._gen = 0
        self._gen_lock = threading.Lock()
        # Measured from the live output stream (PortAudio reports the device's
        # own buffering), so a Bluetooth speaker widens it automatically instead
        # of needing a hand-tuned constant.
        self._output_latency_s = 0.0
        # One stream per UTTERANCE, not per sentence. Opening a device costs
        # real time on Bluetooth (the link renegotiates) and can click between
        # sentences — which is audible precisely at the boundaries the prefetch
        # pipeline just finished smoothing out.
        self._stream = None
        self._stream_sr = None
        # /voice/tts_speaking is edge-triggered from TWO threads (the worker and
        # the stop callback), so the flag is instance state behind a lock. It was
        # a local in _run(): a stop cleared the queue including the utterance's
        # <|eou|>, so the worker never published False and stt_node muted the mic
        # forever (stt_node.py drops every frame while tts_speaking is true).
        self._speaking = False
        self._speaking_lock = threading.Lock()
        # Once the cloud provider has failed inside an utterance, stay on the
        # local voice until the utterance ends. Retrying per sentence made the
        # robot change voice mid-reply and then change back, which sounds like
        # a fault even though both halves worked.
        self._forced_fallback = False
        self._synth_thread = threading.Thread(target=self._synth_loop, daemon=True)
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._synth_thread.start()
        self._play_thread.start()

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
        # '' matches every name, so an unset hint used to take device 0 without
        # warning — wrong speaker, no clue why.
        hint = (hint or '').strip()
        if not hint:
            self.get_logger().info('no output_device hint — using the system default')
            return None
        for i, d in enumerate(sd.query_devices()):
            if hint.lower() in d['name'].lower() and d['max_output_channels'] > 0:
                return i
        self.get_logger().warning(f'no output device matching {hint!r}; using system default')
        return None

    def _on_speech(self, msg: String):
        self._q.put(msg.data)

    def _on_stop(self, msg: String):
        """Abandon everything queued and playing, right now."""
        self._bump_generation()
        self._interrupt.set()
        for q in (self._q, self._audio_q):
            with q.mutex:
                q.queue.clear()
                q.not_full.notify_all()      # the synth thread may be blocked here
        # The queues we just dropped may have held this utterance's <|eou|>, and
        # that marker is the only thing that unmutes the mic. Release it here.
        self._close_stream()
        self._forced_fallback = False
        self._set_speaking(False)

    def _bump_generation(self) -> int:
        """Invalidate audio belonging to the utterance being abandoned.

        With synthesis running ahead of playback, clearing the queues is not
        enough: a buffer synthesised before the stop may already be in the play
        thread's hand. Every buffer carries the generation it was made in, and
        anything stale is dropped rather than spoken.
        """
        with self._gen_lock:
            self._gen += 1
            return self._gen

    @property
    def _generation(self) -> int:
        with self._gen_lock:
            return self._gen

    def _set_speaking(self, on: bool) -> None:
        """Publish /voice/tts_speaking on edges only. Safe from any thread."""
        with self._speaking_lock:
            if self._speaking == on:
                return
            self._speaking = on
        self._speaking_pub.publish(Bool(data=on))

    # ── Synthesis thread: always one sentence ahead of the speaker ─────────

    def _synth_loop(self):
        """Turn text into audio and hand it to the player.

        Synthesis used to happen inline with playback, so every sentence
        boundary was dead air the length of the next synthesis — measured 1470ms
        on Sarvam and RTF ~1.8 on local Kokoro (a 3s sentence took 5.4s). Running
        it here, one buffer ahead, hides that behind the audio already playing.
        The queue is bounded: getting far ahead only costs memory and makes a
        barge-in slower to take effect.
        """
        while rclpy.ok():
            text = self._q.get()
            if text == SPEECH_EOU:
                self._audio_q.put((self._generation, None, None))
                continue
            # A sentence arriving now belongs to a NEW utterance.
            self._interrupt.clear()
            gen = self._generation
            try:
                audio = self._synthesize(text)
            except Exception:
                # Belt and suspenders: an escaped exception must not kill this
                # thread, or every later sentence is silently dropped forever.
                self.get_logger().error(
                    f'unhandled error synthesising {text!r}\n{traceback.format_exc()}')
                continue
            if audio is None or gen != self._generation:
                continue                      # failed, or abandoned mid-synthesis
            self._audio_q.put((gen, audio[0], audio[1]))

    # ── Playback thread ───────────────────────────────────────────────────

    def _play_loop(self):
        while rclpy.ok():
            gen, samples, sr = self._audio_q.get()
            if gen != self._generation:
                continue                      # stale: belongs to a stopped utterance
            if samples is None:               # end-of-utterance marker
                # Wait out the device buffer before declaring silence. write()
                # returns when the last sample is HANDED OVER, not when it is
                # heard; Bluetooth adds another 100-250ms on top. Releasing the
                # mic too early made the robot transcribe its own tail and
                # answer itself — a real feedback loop, observed 2026-09-05.
                drain = self._output_latency_s
                if drain > 0:
                    time.sleep(drain)
                self._close_stream()      # let the speaker idle between turns
                self._forced_fallback = False
                self._set_speaking(False)
                continue
            self._set_speaking(True)
            try:
                self._play(samples, sr, gen)
            except Exception:
                self.get_logger().error(f'unhandled playback error\n{traceback.format_exc()}')

    def _synthesize(self, text: str):
        """Text -> (samples, sr), or None if every provider failed."""
        provider, fell_back = self._provider.name, False
        t0 = time.monotonic()
        if self._forced_fallback and self._provider is not self._fallback:
            samples, sr = self._fallback.synthesize(text)
            self._publish_tts_meta(self._fallback.name, True, t0, text, samples, sr, ok=True)
            return samples, sr
        try:
            samples, sr = self._provider.synthesize(text)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{self._provider.name} failed ({e}); falling back to local')
            fell_back = True
            self._forced_fallback = True
            provider = self._fallback.name
            try:
                samples, sr = self._fallback.synthesize(text)
            except Exception:
                # rclpy's logger has no .exception() (stdlib logging does) — format manually or
                # this masks the real error behind an AttributeError. Found live 2026-09-04.
                self.get_logger().error(f'local fallback synthesis also failed for: {text!r}\n{traceback.format_exc()}')
                self._publish_tts_meta(provider, fell_back, t0, text, None, None, ok=False)
                return None
        except Exception:
            self.get_logger().error(f'synthesis failed for: {text!r}\n{traceback.format_exc()}')
            self._publish_tts_meta(provider, fell_back, t0, text, None, None, ok=False)
            return None
        self._publish_tts_meta(provider, fell_back, t0, text, samples, sr, ok=True)
        return samples, sr

    def _publish_tts_meta(self, provider: str, fell_back: bool, t0: float, text: str,
                          samples, sr, ok: bool) -> None:
        """One JSON line per synthesised sentence — cost, not content.

        Only the character count of the text goes out; what the robot said is
        already on /voice/robot_speech for anyone who wants it.
        """
        latency_ms = int((time.monotonic() - t0) * 1000)
        audio_ms = int(len(samples) / sr * 1000) if ok and sr else 0
        meta = {
            'provider': provider,
            'configured_provider': self._provider.name,
            'fell_back': fell_back,
            'ok': ok,
            'latency_ms': latency_ms,
            'audio_ms': audio_ms,
            # >1 means synthesis is slower than real time — the speaker waits.
            'rtf': round(latency_ms / audio_ms, 2) if audio_ms else None,
            'chars': len(text),
        }
        try:
            self._tts_meta_pub.publish(String(data=json.dumps(meta)))
        except Exception:
            self.get_logger().debug('tts_meta publish failed')

    def _open_stream(self, sr: int):
        """Reuse the open stream when the sample rate matches, else reopen."""
        if self._stream is not None and self._stream_sr == sr:
            return self._stream
        self._close_stream()
        self._stream = sd.OutputStream(samplerate=sr, channels=1, dtype='float32',
                                       device=self._out_device)
        self._stream.start()
        self._stream_sr = sr
        self._output_latency_s = float(getattr(self._stream, 'latency', 0.0) or 0.0)
        return self._stream

    def _close_stream(self):
        stream, self._stream, self._stream_sr = self._stream, None, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                self.get_logger().debug('closing output stream failed')

    def _play(self, samples: np.ndarray, sr: int, gen: int = 0):
        chunk_frames = int(sr * CHUNK_FRAMES_S)
        try:
            stream = self._open_stream(sr)
            for i in range(0, len(samples), chunk_frames):
                if self._interrupt.is_set() or gen != self._generation:
                    break
                stream.write(samples[i:i + chunk_frames])
        except Exception:
            # A Bluetooth speaker that sleeps or wanders out of range takes the
            # stream with it. Drop it so the next sentence opens a fresh one
            # rather than writing into a dead handle forever.
            self._close_stream()
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
