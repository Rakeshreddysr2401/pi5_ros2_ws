# Jetson Voice Upgrade — Siri/Alexa-grade listening

> **STATUS: IMPLEMENTED 2026-07-03** on the Jetson, branch
> `dev-1.0.4_voice_upgrade` of the `speech_vision` repo (~/robot). All five
> changes below are live and verified (wake model 8/8 on synthesized voices,
> Silero rejects white noise, music voice-loop round-trip works). Remaining
> human work:
> 1. **Say "hey jarvis"** for now — train the custom `hey_rakhi` model in the
>    openWakeWord Colab (~1hr), drop it in `/model_store/wake/`, and change
>    `wake_models: ["hey_rakhi"]` in voice_params.yaml.
> 2. On-mic acoustic tests (stop over TTS from across the room, barge-in
>    feel, wake range) — tune `wake_threshold` 0.3–0.7 from experience.
> This document remains the design reference.

Change spec for the `speech_vision` repo (`ai_ws/src/voice_pkg`), written against
branch `dev-1.0.3_with_fable`. The Pi5 side is **already done and deployed**
(music tools, `/audio/*` topic contract, barge-in turn abort).

## Why the current pipeline falls short (diagnosis of the actual code)

| Problem you feel | Root cause in code |
|---|---|
| Robot triggers on background noise | `webrtcvad` (stt_node.py:97) is an energy detector — TV, music, keyboard clicks all read as "speech" and get transcribed |
| Robot answers speech not addressed to it | Wake word is **transcript-based** (`wake_gate.py`): everything in the room is transcribed first, THEN text-filtered. Whisper misspells "Rakhi" 7 ways, and the 15s attention window (stt_node.py:54) forwards ALL room chatter after the robot speaks |
| Echo / hears itself | **No AEC.** The mic is simply muted while TTS plays (`_tts_cb` drops everything) — half-duplex, like a walkie-talkie, not like Alexa |
| "Stop" unreliable during speech | The "barge-in lite" spotter (stt_node.py:19-27, 204-232) is a workaround for the missing AEC: it can only accept ≤1.5s isolated bursts and needs a self-echo guard in tts_node (:115-133) |
| Latency | Every gate decision costs a full Whisper transcription; nothing is rejected before STT |

**The one structural fix is AEC.** With echo cancellation, the mic stays live
while the robot speaks or plays music, the duration-hack spotter becomes a
clean keyword check, and true barge-in works. Everything else layers on top.

---

## Target pipeline (what Alexa/Siri actually do)

```
                        ┌────────────────────────────────────────────┐
speaker ◄── TTS/music ──┤  PipeWire echo-cancel module               │
                        │  (playback = AEC reference signal)         │
mic ────────────────────┤  → echo-cancelled mic source               │
                        └───────────────┬────────────────────────────┘
                                        ▼
                        openWakeWord  (neural KWS, ~80ms frames, always on)
                          ├─ "hey rakhi" → open capture window
                          └─ "stop"      → /voice/tts_stop + music stop (LOCAL, <400ms)
                                        ▼ (only when awake)
                        Silero VAD  (neural, noise-robust endpointing)
                                        ▼
                        Whisper (existing whisper_cuda backend, unchanged)
                                        ▼
                        wake_gate (kept as SECONDARY text check) → /voice/user_input
```

Key inversion: today it's *transcribe everything → filter by text*; the target
is *reject almost everything before STT* — the wake model runs on raw audio at
negligible CPU and only opens the mic window when addressed.

---

## Change 1 — AEC via PipeWire echo-cancel (do this first; biggest win)

The Jetson already runs PipeWire (`pw-cat` paths in audio_capture.py). PipeWire
ships WebRTC echo cancellation as a loadable module: it creates a virtual
**source** (mic minus whatever is being played) and a virtual **sink** (play
through here = becomes the cancellation reference).

**System config** — create `~/.config/pipewire/pipewire.conf.d/99-echo-cancel.conf`:

```
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      aec.method = webrtc
      # denoise + AGC come free with the webrtc canceller:
      aec.args = { webrtc.gain_control = true webrtc.noise_suppression = true }
      capture.props  = { node.name = "ec_mic"     node.description = "Echo-cancelled mic" }
      playback.props = { node.name = "ec_speaker" node.description = "Echo-cancelled out" }
    }
  }
]
```
Restart PipeWire (`systemctl --user restart pipewire wireplumber`), verify with
`pw-cli ls Node | grep ec_` that `ec_mic` and `ec_speaker` exist.
(If the module is missing: `sudo apt install pipewire-audio` / check
`libpipewire-module-echo-cancel.so` exists; it's standard on Ubuntu 22.04+ PipeWire.)

**Code changes (all device plumbing, no logic):**

1. `audio_capture.py` — `PipeWireCapture._spawn()`: add
   `'--target', 'ec_mic'` to the pw-cat args so capture reads the
   echo-cancelled source. Make pw-cat the DEFAULT capture path (today it's the
   fallback when no ALSA device is found — flip the preference: AEC only exists
   in PipeWire).
2. `tts_backend.py` — `KokoroBackend.speak()`: make the pw-cat playback path
   the default and add `'--target', 'ec_speaker'` — TTS audio MUST route
   through the echo-cancel sink or it isn't part of the reference signal.
3. `music_node` (Change 5) also plays to `ec_speaker`.
4. `stt_node._tts_cb`: **stop muting the mic** while TTS speaks — with AEC the
   robot's own voice is subtracted. Keep `/voice/tts_speaking` publishing (the
   Pi5 uses it), but the audio loop now processes every chunk through the wake
   engine regardless of TTS state. Delete the `_spot_*` machinery (the
   duration-cap workaround) once Change 4 lands.

**Acceptance:** play a long TTS sentence, speak over it, record `ec_mic`
(`pw-cat --record --target ec_mic test.wav`) — your voice should be clear, the
robot's voice nearly gone.

---

## Change 2 — Real wake word: openWakeWord (kills the false triggers)

Transcript gating can't be fixed by tuning aliases — the fix is a neural
keyword spotter on raw audio, before STT ever runs.

**Install:** `pip install openwakeword` (ONNX runtime, ~1ms per 80ms frame on
Orin CPU — negligible).

**Model:** train a custom **"hey rakhi"** model with openWakeWord's Colab
notebook (github.com/dscripka/openWakeWord → "training new models", ~1 hour,
synthetic data only, no recordings needed). Until it's trained, bootstrap with
the pre-trained `"hey jarvis"` model to validate the plumbing (say "hey jarvis"
instead of "Rakhi" for a day).

**Integration (stt_node.py audio loop):**

```python
from openwakeword.model import Model as WakeModel
self._wake = WakeModel(wakeword_models=["hey_rakhi.onnx", "stop.onnx"],
                       inference_framework="onnx")
WAKE_THRESHOLD = 0.5   # tune 0.3–0.7 against real room audio

def _audio_loop(self):
    while rclpy.ok():
        chunk = self._capture.read(timeout=1.0)     # 1280 samples = 80ms, int16
        if chunk is None: continue
        scores = self._wake.predict(chunk)          # feed EVERY chunk, always
        if scores["stop"] > WAKE_THRESHOLD and (self._tts_speaking or self._music_playing):
            self._on_stop_detected()                # Change 4 — local, instant
        if scores["hey_rakhi"] > WAKE_THRESHOLD:
            self._open_capture_window()             # start recording an utterance
        with self._lock:
            if self._capture_window_open:
                self._process(chunk)                # existing VAD→STT path
```

**Semantics changes:**
- `_process()` runs ONLY inside a capture window (wake word heard, or attention
  window open). Everything else is never transcribed — this alone removes the
  ambient-chatter answers AND cuts idle GPU/CPU use.
- Shrink `attention_s` from 15.0 → **6.0** and make it start ONLY after the
  robot asked a question or answered (it already re-arms in `_tts_cb`). With a
  reliable wake word, a short window is enough; the long window was
  compensating for Whisper misspelling "Rakhi".
- Keep `wake_gate()` as a SECONDARY check inside the window (belt and braces,
  strips the name from the text) — but it no longer decides alone.
- Wake detection during TTS/music = **barge-in**: call `_on_stop_detected()`'s
  playback-halt half (stop TTS audio, duck music), open the capture window, and
  forward the utterance. The Pi5 already aborts its in-flight turn when the new
  utterance arrives.

---

## Change 3 — Silero VAD replaces webrtcvad (noise robustness)

webrtcvad fires on any energy; Silero is a small neural model that fires on
*human speech*. This fixes endpointing in noisy rooms (fan, TV, music leakage).

**Install:** `pip install silero-vad onnxruntime` (or load via torch.hub; the
ONNX path avoids torch on the audio thread).

```python
from silero_vad import load_silero_vad
self._vad_model = load_silero_vad(onnx=True)

def _is_speech(self, chunk: np.ndarray) -> bool:
    # Silero wants 512-sample frames at 16kHz; chunk is 1280 — evaluate the
    # two full frames and OR them (same pattern as the old 20ms sub-frames).
    f32 = chunk.astype(np.float32) / 32768.0
    return any(self._vad_model(torch.from_numpy(f32[i:i+512]), 16000).item() > 0.5
               for i in (0, 512))
```

Keep `silence_timeout`/`min_speech_duration` params as-is; only the detector
changes. Drop the `webrtcvad` dependency when done.

---

## Change 4 — Instant stop, done properly

Target: spoken **"stop"** halts whatever is happening in <400ms, and the robot
never stops itself with its own voice.

- Detection: the `"stop"` openWakeWord model (train in the same Colab run as
  "hey rakhi"), evaluated on the **echo-cancelled** stream — the robot's own
  "stop" is subtracted by AEC, so the tts_node self-echo guard
  (`_stop_cb`'s `_current_text` check) can be deleted.
- On detection, **act locally first** (no Pi5 round-trip):
  1. music playing → `music_node.stop()` (Change 5 exposes this internally)
  2. TTS speaking → existing `_stop_cb` flush path in tts_node
  3. THEN publish `/voice/tts_stop` — the Pi5 stops wheels + sweeps music
     state (already implemented brain-side).
- Delete the Whisper-based `_spot_*` path in stt_node (lines 19-27, 77-87,
  204-232, 270-284) once the KWS model is validated — it was the no-AEC
  workaround.

**Interim step** (before the "stop" model is trained): keep the existing spot
path but feed it echo-cancelled audio and drop `_SPOT_MAX_S` gymnastics — with
AEC the mic no longer hears the robot, so plain short-burst keyword checks
become reliable.

---

## Change 5 — music_node (new file: `voice_pkg/music_node.py`)

Music playback with instant local stop and TTS ducking. The Pi5 chat agent
already has `play_music/stop_music/pause_music/resume_music/set_music_volume`
tools speaking this exact contract.

**Engine:** `mpv` with JSON IPC + `yt-dlp` for search/resolution.
Install: `sudo apt install mpv` · `pip install yt-dlp python-mpv-jsonipc`

**Topic contract (must match exactly — Pi5 side is live):**

Subscribe `/audio/music_cmd` (std_msgs/String, JSON):
```json
{"action": "play",   "query": "shape of you", "t": 1783071000.0}
{"action": "pause",  "t": ...}
{"action": "resume", "t": ...}
{"action": "stop",   "t": ...}
{"action": "volume", "level": 60, "t": ...}
```

Publish `/audio/music_state` (std_msgs/String, JSON) — on every change AND at
1Hz while playing:
```json
{"playing": true, "paused": false, "title": "Ed Sheeran - Shape of You",
 "volume": 70, "error": null, "stamp": 1783071004.2, "cmd_t": 1783071002.7}
```
On a failed play: `{"playing": false, ..., "error": "no results for ...", "cmd_t": ...}`.
`cmd_t` echoes the play command's `t` verbatim — the Pi5's `play_music` tool
confirms on `cmd_t == the t it sent` (an opaque token, 10s timeout). Never
compare `stamp` against the Pi5 clock: the two machines drift ~1.5s, and a 1Hz
heartbeat of a previous song must not confirm a new request.

**Behaviour:**
- `play`: resolve query via yt-dlp (`ytsearch1:<query>`, extract audio stream
  URL + title), load into mpv (`--no-video --audio-device=pipewire` with target
  `ec_speaker` — music MUST be in the AEC reference), publish state.
- **Ducking:** subscribe `/voice/tts_speaking` — while True, drop mpv volume to
  ~25% of current; restore after. The robot talks over quiet music, like Alexa.
- **Stop priority:** expose a process-local `stop()` used by the stt_node
  keyword path (same process or via a latched local topic) so stop latency is
  playback-only (~100ms), then reflect it in `/audio/music_state`.
- mpv process supervised: if it dies, publish `error` state and respawn on next
  play.

**Skeleton:**

```python
class MusicNode(Node):
    def __init__(self):
        super().__init__('music_node')
        self._mpv = None          # spawned lazily on first play
        self._state = {"playing": False, "paused": False, "title": "",
                       "volume": 70, "error": None}
        self.create_subscription(String, '/audio/music_cmd', self._cmd_cb, 10)
        self.create_subscription(Bool, '/voice/tts_speaking', self._duck_cb, 10)
        self._state_pub = self.create_publisher(String, '/audio/music_state', 10)
        self.create_timer(1.0, self._tick)   # 1Hz state while playing

    def _cmd_cb(self, msg):
        cmd = json.loads(msg.data)
        {"play": self._play, "pause": self._pause, "resume": self._resume,
         "stop": self._stop, "volume": self._volume}[cmd["action"]](cmd)

    def _play(self, cmd):
        info = yt_dlp.YoutubeDL({'format': 'bestaudio', 'noplaylist': True,
                                 'quiet': True}).extract_info(
                                 f"ytsearch1:{cmd['query']}", download=False)
        entry = info['entries'][0]
        self._ensure_mpv().play(entry['url'])
        self._publish_state(playing=True, paused=False, title=entry['title'], error=None)
```

Add `music_node` to `voice.launch.py` and a `console_scripts` entry in setup.py.

---

## Full topic contract after the upgrade (Pi5 ⇄ Jetson)

| Topic | Type | Dir | Notes |
|---|---|---|---|
| `/voice/user_input` | String | J→P | wake-gated transcripts only (incl. barge-in utterances) |
| `/voice/robot_speech` | String | P→J | sentence chunks + `<\|eou\|>` (unchanged) |
| `/voice/tts_speaking` | Bool | J→P | now also drives music ducking (unchanged wire) |
| `/voice/tts_stop` | String | J→P | stop keyword — Pi5 halts wheels + sweeps music (unchanged wire) |
| **`/audio/music_cmd`** | String JSON | P→J | **new** — see Change 5 |
| **`/audio/music_state`** | String JSON | J→P | **new** — on change + 1Hz while playing |
| `/camera/...`, `/vision/...`, `/diag/timing` | | | unchanged |

No existing wire format changes — the Pi5 does not need a rebuild for any of
this (the `/audio/*` handling is already deployed).

---

## Rollout order & acceptance tests

Do it in this order — each step is independently shippable:

| Step | Change | Test that proves it |
|---|---|---|
| 1 | AEC (Change 1) | Speak over the robot's TTS; your recorded `ec_mic` audio is clean; "stop" spot works mid-sentence without the duration hack |
| 2 | Silero VAD (Change 3) | TV/music playing in room: robot does not start recording; your speech still endpointed correctly |
| 3 | openWakeWord (Change 2) | 30 min of room conversation without "Rakhi": zero forwards to Pi5 (check `journalctl` on Pi5: no turns). "Hey Rakhi what time is it" from 4m away: answered |
| 4 | Stop KWS (Change 4) | Robot mid-sentence, say "stop": silence within 400ms. Robot says the word "stop" in a reply: it does NOT stop itself |
| 5 | music_node (Change 5) | "Rakhi play shape of you" → correct song within ~6s, Pi5 confirms real title. "Rakhi, what's playing?" answered from NOW PLAYING. Speak to it while music plays: it hears you (AEC). "Stop" → music halts <400ms |

**Latency budgets** (measure via existing `/diag/timing` events):
wake→listening ≤200ms · stop→silence ≤400ms · barge-in→robot silent ≤500ms ·
play command→audio ≤6s (yt-dlp bound).

## What NOT to change

- The `/voice/robot_speech` chunk + `<|eou|>` protocol and `/voice/tts_speaking`
  semantics — the Pi5 streaming pipeline depends on them byte-for-byte.
- `whisper_cuda` backend — it's already the right STT; it just runs far less
  often once the wake engine gates it.
- `wake_gate.py` — keep as the secondary in-window text check (it strips the
  name from forwarded text, which the brain's prompts expect).

## Hardware note (unchanged advice, still true)

Software AEC gets you ~90% of the way. The P1 **USB conference speakerphone
with hardware AEC** (~$35-70) remains worth buying: it does cancellation in
silicon with a matched mic/speaker pair, works at higher volumes, and adds a
far-field mic array. With one, Change 1 becomes "select the device" and
everything else here stays identical.
