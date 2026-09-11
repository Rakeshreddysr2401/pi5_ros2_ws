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

Wake word: an acoustic detector (wake/, e.g. openWakeWord) gates the mic. While
ASLEEP the node runs only the cheap detector and transcribes NOTHING — no
ambient speech ever reaches a cloud STT provider (cost + privacy). On the wake
word it goes LISTENING: transcribe the command, publish it, hold a short
follow-up window (so you can keep talking without re-saying the wake word),
then sleep. If no acoustic model is configured/available it degrades to the
legacy "transcribe everything, gate on a name in the transcript" mode
(wake_detector: transcript_alias) — never crashes (CLAUDE.md #4).

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
import json
import os
import queue
import re
import threading
import time
import traceback

import numpy as np
import rclpy
import sounddevice as sd
import webrtcvad
from dotenv import load_dotenv
from rclpy.node import Node
from std_msgs.msg import Bool, String

from . import bt_audio
from .addressing import strip_alias
from .vad_gate import GateConfig, evaluate as gate_utterance
from .stt_providers import REGISTRY, ProviderUnavailable
from .stt_providers.local_whisper import LocalWhisperProvider
from .wake import REGISTRY as WAKE_REGISTRY, WakeUnavailable

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
PRE_PAD_FRAMES = 10       # ~300ms of audio kept before speech is confirmed
END_SILENCE_FRAMES = 20   # ~600ms of silence ends the utterance
MIN_UTTERANCE_FRAMES = 10  # ~300ms — drop blips shorter than this
# Utterances waiting to be transcribed, and how old one may get before
# answering it would be worse than silence.
STT_QUEUE_DEPTH = 3
STALE_UTTERANCE_S = 15.0
TTS_TAIL_MUTE_S = 0.5      # keep muting briefly after TTS stops (speaker echo tail)


class STTNode(Node):
    def __init__(self):
        super().__init__('pi5_stt_node')
        load_dotenv(os.path.expanduser(os.getenv('LANGROBO_ENV_FILE', '~/ros2_ws/.env')))

        self.declare_parameter('input_device', 'Blackwire')
        # Bluetooth. Only bt_profile=hfp gives a MIC over Bluetooth (8-16kHz
        # mono — Whisper accuracy drops); with a2dp the speaker is output-only
        # and this node keeps using the wired mic.
        self.declare_parameter('bt_mac', '')
        self.declare_parameter('bt_profile', 'a2dp')
        # PipeWire resets the HFP mic to its own level on every reconnect, and
        # this speaker's is far below what min_utterance_rms expects — see
        # voice_params.yaml. Re-applied inside bt_audio.ensure(), not by hand.
        self.declare_parameter('bt_mic_gain', 1.0)
        # How long to keep the mic muted after TTS stops. Was a hardcoded 0.5s
        # tuned for a USB headset. A2DP buffers 100-250ms and _play returns when
        # the last sample is WRITTEN, not heard — so over Bluetooth the speaker
        # is still talking when this expires and the robot transcribes itself.
        # Raise it to ~1.2 for a Bluetooth speaker.
        self.declare_parameter('tts_tail_mute_s', TTS_TAIL_MUTE_S)
        self.declare_parameter('model_size', 'base')
        self.declare_parameter('model_dir', '')
        self.declare_parameter('threads', 4)
        self.declare_parameter('vad_aggressiveness', 2)
        # Seconds between [diag] lines. These ran unconditionally at ~1s, which
        # is ~86k INFO lines a day into journald on a robot meant to run 24/7.
        # 0 turns them off.
        self.declare_parameter('diag_log_period_s', 10.0)
        # Noise gate between the VAD and the recogniser (see vad_gate.py). A
        # room mic hands the cloud plenty of non-speech, and it answers with
        # confident invented sentences rather than nothing.
        # 0.05, not the 0.012 this shipped with: the Bluetooth mic's measured
        # noise floor is ~0.029, so 0.012 passed every idle-room segment
        # straight through to the cloud recogniser, which answers noise with a
        # confident invented sentence. vad_gate.GateConfig's own default was
        # corrected on 2026-09-05; this parameter default was not, and it
        # OVERRIDES it — so anyone running the node without voice_params.yaml
        # (run_stt.sh with a different params file, `ros2 run` by hand) still
        # got the broken gate. Keep the two in step.
        self.declare_parameter('min_utterance_rms', 0.05)
        self.declare_parameter('min_voiced_ratio', 0.35)
        # Hard stop on a single utterance. Without it a television, a fan or a
        # conversation in the room keeps the VAD in-speech indefinitely: the
        # buffer grows without bound and whatever finally gets sent is a huge,
        # expensive, useless cloud call. Cutting means the speaker gets
        # transcribed in pieces, which is far better than not at all.
        self.declare_parameter('max_utterance_s', 20.0)
        self.declare_parameter('wake_aliases', ['rakhi', 'chotu', 'hey pi'])
        self.declare_parameter('stop_words', ['stop'])
        self.declare_parameter('stt_provider', 'local')       # local | sarvam | soniox
        self.declare_parameter('stt_source_language', 'te')   # Telugu source for cloud translate
        self.declare_parameter('stt_target_language', 'en')
        # Wake word: 'transcript_alias' = legacy (transcribe all, match a name in text);
        # 'openwakeword' = acoustic gate (transcribe nothing until the word is heard).
        self.declare_parameter('wake_detector', 'transcript_alias')
        self.declare_parameter('wake_word', 'hey_jarvis')  # bundled stand-in until rakhi.onnx
        self.declare_parameter('wake_model_path', '')      # explicit .onnx (custom Rakhi) wins
        self.declare_parameter('wake_threshold', 0.5)
        self.declare_parameter('follow_up_window_s', 9.0)  # stay awake this long after each command
        # transcript_alias mode only: if False, forward EVERY transcript to the brain
        # (no name required in the text — the agent prompt knows its own name and judges
        # relevance). Ignored in acoustic mode, where the wake word already gates.
        self.declare_parameter('require_wake', True)

        device_hint = self.get_parameter('input_device').value
        model_size = self.get_parameter('model_size').value
        model_dir = self.get_parameter('model_dir').value
        threads = int(self.get_parameter('threads').value)
        self._vad = webrtcvad.Vad(int(self.get_parameter('vad_aggressiveness').value))
        self._max_utterance_frames = int(
            float(self.get_parameter('max_utterance_s').value) * 1000 / FRAME_MS)
        self._gate = GateConfig(
            min_frames=MIN_UTTERANCE_FRAMES,
            min_rms=float(self.get_parameter('min_utterance_rms').value),
            min_voiced_ratio=float(self.get_parameter('min_voiced_ratio').value))
        _diag_s = float(self.get_parameter('diag_log_period_s').value)
        self._diag_every = int(_diag_s * 1000 / FRAME_MS) if _diag_s > 0 else 0
        self._aliases = [a.lower() for a in self.get_parameter('wake_aliases').value]
        self._stop_words = [w.lower() for w in self.get_parameter('stop_words').value]
        self._require_wake = bool(self.get_parameter('require_wake').value)
        provider_name = self.get_parameter('stt_provider').value
        src_lang = self.get_parameter('stt_source_language').value
        tgt_lang = self.get_parameter('stt_target_language').value

        self._tail_mute_s = float(self.get_parameter('tts_tail_mute_s').value)
        bt_mac = self.get_parameter('bt_mac').value
        bt_profile = self.get_parameter('bt_profile').value
        if bt_mac:
            bt = bt_audio.ensure(bt_mac, bt_profile,
                                 float(self.get_parameter('bt_mic_gain').value))
            for note in bt['notes']:
                self.get_logger().info(f'bluetooth: {note}')
            if bt_profile == 'hfp' and bt['source']:
                device_hint = 'pipewire'
                self.get_logger().info(
                    f"bluetooth source {bt['source']!r} is default — mic via pipewire")
            elif bt_profile == 'hfp':
                self.get_logger().warning(
                    f'bluetooth mic unavailable; falling back to {device_hint!r}')
            if self._tail_mute_s < 1.0:
                # The exact failure this causes is the robot answering itself.
                self.get_logger().warning(
                    f'tts_tail_mute_s={self._tail_mute_s}s is short for Bluetooth '
                    '(A2DP buffers 100-250ms) — the mic may catch the tail of '
                    'the robot\'s own speech. ~1.2 is safer.')

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
        # Which provider transcribed this utterance and what it cost. Published
        # JUST BEFORE /voice/user_input so agent_node can staple it onto the
        # turn's LangSmith trace — otherwise a trace starts at the brain and the
        # STT leg (local vs Sarvam vs a silent fallback to local) is invisible.
        self._stt_meta_pub = self.create_publisher(String, '/voice/stt_meta', 10)

        # Don't listen to ourselves: mute capture while the robot is speaking (+ a short
        # echo tail). Without this, forward-all mode transcribes the robot's own TTS and
        # feeds it back into the brain — an endless self-conversation (no AEC on the Pi5).
        self._tts_speaking = False
        self._mute_until = 0.0
        self.create_subscription(Bool, '/voice/tts_speaking', self._on_tts_speaking, 10)

        self._voiced_frames = 0
        self._ring: collections.deque = collections.deque(maxlen=PRE_PAD_FRAMES)
        self._utterance: list[bytes] = []
        self._in_speech = False
        self._silence_run = 0
        self._dbg_frames = 0  # [diag 2026-09-04] audio-callback heartbeat counter

        # Wake gate. self._wake is None => legacy transcript_alias mode (transcribe
        # everything). Otherwise acoustic: asleep until the word is heard, then a
        # follow-up window keeps us listening for back-to-back commands.
        self._wake_label = ''
        self._wake = self._build_wake(
            self.get_parameter('wake_detector').value,
            {'wake_word': self.get_parameter('wake_word').value,
             'wake_model_path': self.get_parameter('wake_model_path').value,
             'wake_threshold': self.get_parameter('wake_threshold').value},
        )
        self._acoustic = self._wake is not None
        self._awake = False
        self._awake_until = 0.0
        self._wake_peak = 0.0  # [diag] peak wake score since last heartbeat log
        self._follow_up_s = float(self.get_parameter('follow_up_window_s').value)
        if self._acoustic:
            self.get_logger().info(
                f'wake_detector = {self._wake.name} ({self._wake_label!r}); '
                f'asleep until wake word, {self._follow_up_s:.0f}s follow-up window')
        else:
            self.get_logger().info(
                'wake_detector = transcript_alias (no acoustic model — transcribes '
                'everything and matches a name in the text)')

        # Bounded, and stamped: if transcription falls behind (a slow cloud
        # call, several people talking), the backlog is answered minutes late,
        # which reads as the robot randomly bringing up old topics. Keep the
        # newest few and drop anything that waited too long.
        self._queue: queue.Queue = queue.Queue(maxsize=STT_QUEUE_DEPTH)
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

    def _build_wake(self, name: str, params: dict):
        """Return a WakeDetector, or None to run legacy transcript_alias mode."""
        if name in (None, '', 'transcript_alias', 'none'):
            return None
        cls = WAKE_REGISTRY.get(name)
        if cls is None:
            self.get_logger().error(f'unknown wake_detector {name!r}; using transcript_alias')
            return None
        try:
            det = cls.from_config(params, os.environ)
            self._wake_label = params.get('wake_model_path') or params.get('wake_word')
            return det
        except WakeUnavailable as e:
            self.get_logger().warning(
                f'{name} wake detector unavailable ({e}); using transcript_alias')
            return None

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
        self._dbg_frames += 1

        # Self-hearing guard: drop audio while TTS speaks (+ tail) so we don't transcribe
        # and re-send the robot's own reply. Discard any partially-captured utterance too.
        if self._tts_speaking or time.monotonic() < self._mute_until:
            if self._in_speech or self._utterance:
                self._reset_capture()
            # Keep the pre-roll warm. This return used to skip _ring.append, so
            # the ring was empty at unmute and the ~300ms pre-pad was gone — on
            # top of TTS_TAIL_MUTE_S that clipped up to 800ms off a prompt
            # reply, which is how 'Yes, do that' reaches the brain as 'do that'.
            # The ring costs nothing here: no VAD, no transcription.
            self._ring.append(frame)
            return

        # ── ASLEEP (acoustic mode): only the cheap wake detector runs, no transcription ──
        if self._acoustic and not self._awake:
            try:
                fired = self._wake.process(frame)
                self._wake_peak = max(self._wake_peak, getattr(self._wake, 'last_score', 0.0))
                if fired:
                    self._awake = True
                    self._awake_until = time.monotonic() + self._follow_up_s
                    # Clear any half-captured segment, but KEEP the pre-roll
                    # ring: _reset_capture() emptied it too, so the first
                    # ~300ms after the wake word had no pre-pad and the start
                    # of the very command we just woke up for was clipped
                    # ("hey jarvis, go to the kitchen" → "to the kitchen").
                    # The wake detector fires at the END of the wake word, so
                    # the ring holds the run-in to the command, not the word.
                    self._utterance = []
                    self._in_speech = False
                    self._silence_run = 0
                    self._voiced_frames = 0
                    self.get_logger().info(f'wake word {self._wake_label!r} detected — listening')
                    self._debug_vad_pub.publish(String(data=f'wake: {self._wake_label} — listening'))
            except Exception:
                self.get_logger().error(f'wake detector failed\n{traceback.format_exc()}')
            # [diag] peak wake score over the last ~1s — say the wake word and watch this
            # climb toward the threshold; tune wake_threshold from what you see here.
            if self._diag_every and self._dbg_frames % self._diag_every == 0:
                self.get_logger().info(f'[diag] asleep peak_wake_score={self._wake_peak:.2f}')
                self._wake_peak = 0.0
            return

        # ── LISTENING (acoustic awake) or legacy transcript_alias: VAD-segment speech ──
        voiced = self._vad.is_speech(frame, SAMPLE_RATE)
        if self._diag_every and self._dbg_frames % self._diag_every == 0:
            self.get_logger().info(
                f'[diag] frames={self._dbg_frames} voiced={voiced} '
                f'in_speech={self._in_speech} awake={self._awake}')

        if not self._in_speech:
            self._ring.append(frame)
            if voiced:
                self._in_speech = True
                self._utterance = list(self._ring)
                self._silence_run = 0
                self._voiced_frames = 1
            elif self._acoustic and time.monotonic() > self._awake_until:
                # follow-up window elapsed with no new speech → back to sleep
                self._awake = False
                self._wake.reset()
                self.get_logger().info('follow-up window closed — asleep')
                self._debug_vad_pub.publish(String(data='asleep'))
            return

        self._utterance.append(frame)
        if voiced:
            self._silence_run = 0
            self._voiced_frames += 1
        else:
            self._silence_run += 1
        if self._silence_run >= END_SILENCE_FRAMES:
            self._end_utterance()
        elif len(self._utterance) >= self._max_utterance_frames:
            self.get_logger().warning(
                f'utterance hit the {self._max_utterance_frames * FRAME_MS / 1000:.0f}s cap '
                '— cutting it here (continuous noise, or someone talking at length)')
            self._end_utterance()

    def _on_tts_speaking(self, msg: Bool):
        self._tts_speaking = bool(msg.data)
        if not msg.data:
            self._mute_until = time.monotonic() + self._tail_mute_s
            # Restart the follow-up window when the robot STOPS talking. Timing
            # it from the command instead meant a long answer ate the whole
            # window, and the user's reply to a question the robot had just
            # asked arrived after the door had closed.
            if self._awake_until:
                self._awake_until = self._mute_until + self._follow_up_s

    def _reset_capture(self):
        self._ring.clear()
        self._utterance = []
        self._in_speech = False
        self._silence_run = 0
        self._voiced_frames = 0

    def _end_utterance(self):
        frames, self._utterance = self._utterance, []
        voiced_frames, self._voiced_frames = self._voiced_frames, 0
        self._in_speech = False
        self._silence_run = 0
        self._ring.clear()
        if self._acoustic:
            # keep the session alive: reset the follow-up window on every captured command
            self._awake_until = time.monotonic() + self._follow_up_s
        ms = len(frames) * FRAME_MS
        self._debug_vad_pub.publish(String(data=f'utterance detected: {len(frames)} frames (~{ms}ms)'))
        pcm = np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(pcm ** 2))) if pcm.size else 0.0
        verdict = gate_utterance(len(frames), rms, voiced_frames, self._gate)
        if not verdict.keep:
            # Nothing leaves the machine: no cloud call, no invented transcript,
            # no turn. The reason rides on debug_vad so the thresholds are
            # tunable from what you actually see.
            self._debug_vad_pub.publish(String(data=f'dropped: {verdict.reason}'))
            return
        item = (time.monotonic(), pcm)
        try:
            self._queue.put_nowait(item)   # never block the audio callback
        except queue.Full:
            try:
                self._queue.get_nowait()   # drop the oldest, keep the newest
                self._queue.put_nowait(item)
                self.get_logger().warning('transcription backlog — dropped the oldest utterance')
            except queue.Empty:
                pass

    # Words that turn a stop word into something else entirely.
    _STOP_NEGATIONS = ('dont', "don't", 'do not', 'never', 'without', 'not')

    def _is_stop_command(self, low: str) -> bool:
        """True only for an utterance that IS a stop command.

        Substring matching fired on 'don't stop' and 'stop by the shop later' —
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

    def _publish_stt_meta(self, provider: str, fell_back: bool, latency_ms: int,
                          samples: int, text: str) -> None:
        """One JSON line describing this transcription (never the transcript
        itself — that is already on /voice/debug_transcript).

        `fell_back` true means the configured cloud provider failed and local
        Whisper answered instead: the robot stays up (CLAUDE.md #4) but the
        Telugu->English translation is gone, which is exactly the kind of silent
        degradation that is hard to spot without this line.
        """
        audio_ms = int(samples / SAMPLE_RATE * 1000)
        meta = {
            'provider': provider,
            'configured_provider': self._provider.name,
            'fell_back': fell_back,
            'latency_ms': latency_ms,
            'audio_ms': audio_ms,
            # >1 means transcription was slower than the speech it transcribed.
            'rtf': round(latency_ms / audio_ms, 2) if audio_ms else None,
            'chars': len(text),
            'empty': not text,
        }
        try:
            self._stt_meta_pub.publish(String(data=json.dumps(meta)))
        except Exception:
            self.get_logger().debug('stt_meta publish failed')

    def _worker_loop(self):
        while rclpy.ok():
            captured_at, pcm = self._queue.get()
            age = time.monotonic() - captured_at
            if age > STALE_UTTERANCE_S:
                self.get_logger().warning(
                    f'discarding an utterance that waited {age:.1f}s — too old to answer')
                continue
            try:
                self._transcribe(pcm)
            except Exception:
                # rclpy's logger has no .exception() (stdlib logging does) — this call itself
                # used to raise AttributeError and kill the worker thread silently. Found live
                # 2026-09-04 in the TTS-side twin of this bug (tts_node.py _play).
                self.get_logger().error(f'transcribe worker failed\n{traceback.format_exc()}')

    def _transcribe(self, pcm: np.ndarray):
        provider, fell_back = self._provider.name, False
        t0 = time.monotonic()
        try:
            text = self._provider.transcribe(pcm, SAMPLE_RATE)
        except ProviderUnavailable as e:
            self.get_logger().warning(f'{self._provider.name} failed ({e}); falling back to local')
            fell_back = True
            text = self._fallback.transcribe(pcm, SAMPLE_RATE)
            provider = self._fallback.name
        latency_ms = int((time.monotonic() - t0) * 1000)
        text = text.strip()
        self._debug_transcript_pub.publish(String(data=text if text else '(empty — filtered as noise/silence)'))
        self._publish_stt_meta(provider, fell_back, latency_ms, len(pcm), text)
        if not text:
            return

        low = text.lower()
        if self._is_stop_command(low):
            self.get_logger().info(f'stop word heard: {text!r}')
            self._tts_stop_pub.publish(String(data='[stop]'))
            return

        # Forward the whole transcript when either the wake word already gated us
        # (acoustic mode) or name-gating is disabled (require_wake: False) — the agent
        # prompt knows its own name and judges relevance.
        if self._acoustic or not self._require_wake:
            self._forward(text)
            return

        # transcript_alias mode with require_wake: the alias opens the door, and
        # a follow-up window holds it open. Nobody says the robot's name in every
        # sentence of a conversation: "rakhi, set a timer" / "for how long?" /
        # "five minutes" — that third utterance has no alias and used to be
        # dropped, which made every exchange a one-shot command.
        stripped = strip_alias(text, self._aliases)
        if stripped is not None:
            # Bare "Rakhi?" is a real thing people say to get attention; forward
            # the name itself rather than dropping it as an empty remainder.
            self._forward(stripped or text)
            return

        if time.monotonic() < self._awake_until:
            self.get_logger().info(f'follow-up: {text!r}')
            self._forward(text)
            return

        self.get_logger().info(f'not addressed to me — ignored: {text!r}')

    def _forward(self, text: str) -> None:
        """Send a turn to the brain and hold the follow-up window open."""
        self.get_logger().info(f'command: {text!r}')
        self._input_pub.publish(String(data=text))
        self._awake_until = time.monotonic() + self._follow_up_s


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
