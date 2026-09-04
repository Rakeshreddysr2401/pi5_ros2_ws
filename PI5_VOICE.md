# pi5_voice_pkg — CPU-only STT/TTS on the Pi5

Built 2026-09-04 (commit `1eaa106`). Why this exists, what it measured, and
how to run it. Same `/voice/*` wire protocol as the Jetson's `speech_vision`
voice_pkg (`langrobo_core/utils/speech_stream.py` — do not change one side
without the other).

---

## Why

The Jetson's own voice stack (`ai_stack`: whisper_cuda + Kokoro) can't run
while `orin-nav-stack` perception is up — that's an existing, deliberate
decision (`DEPTH_CAMERA.md` on the Jetson, 2026-07-12: the voice stack alone
measured ~6.5GB and cuVSLAM+nvblox don't fit on top of it). Measured again
2026-09-04 with the *current*, heavier stack (cuVSLAM unparked, full
nvblox+nav2 running): GPU bursts to 96–99%, RAM available drops from 3.5GB
idle to 1.6GB, all 6 CPU cores at 44–65%. Confirms the July call, harder than
it was in July — there is no spare capacity on the Orin while it's driving.

The existing fallback is Telegram (`TELEGRAM.md`) — full agent access, no
voice. This package is a second option: run STT/TTS on the Pi5's own idle
CPU (Cortex-A76, 4 cores, near-0 load while driving) instead, so voice keeps
working concurrently with navigation. It does not replace the Jetson voice
stack — that's still the better choice when the robot is parked (its Whisper
is CUDA-accelerated and faster).

---

## What's running

| | |
|---|---|
| STT | `faster-whisper`, model `base`, `compute_type=int8`, 4 threads |
| TTS | `kokoro-onnx`, **fp32** model (not int8 — see below), 4 threads |
| VAD | `webrtcvad`, aggressiveness 2, 30ms frames, ~300ms pre-pad / ~600ms end-silence |
| Wake gate | transcript-contains-alias fallback (`rakhi`/`chotu`/`hey pi`) — no trained wake-word model exists yet for this project (Jetson TODO: `hey_rakhi` training still pending), so this reuses the same fallback mode `speech_vision`'s VOICE_PIPELINE.md documents for when openWakeWord is unavailable |
| Mic/speaker | Plantronics Blackwire C3220 USB headset (already attached to the Pi5) |
| Confidence filter | drop segments where `no_speech_prob > 0.6 AND avg_logprob < -1.0` — the exact fix VOICE_QUALITY.md validated on the Jetson |

Wire protocol (must match `speech_stream.py` / the Jetson's tts_node exactly):

| Topic | Type | Direction |
|---|---|---|
| `/voice/user_input` | `std_msgs/String` | `pi5_stt_node` → `agent_node` |
| `/voice/robot_speech` | `std_msgs/String`, one sentence per message, utterance closed by a message whose data is exactly `<\|eou\|>` | `agent_node` → `pi5_tts_node` |
| `/voice/tts_speaking` | `std_msgs/Bool` | `pi5_tts_node` → `agent_node` (true from first chunk to EOU) |
| `/voice/tts_stop` | `std_msgs/String` | `pi5_stt_node` → both (stop-word barge-in) |

`agent_node._on_user_input` needed **zero changes** — it already subscribes
to `/voice/user_input` by name; this package is just a second publisher on
the same topic, running on the same machine.

---

## STT provider switch — local | Sarvam | Soniox

Added 2026-09-04. `stt_node`'s mic/VAD/wake-gate logic is unchanged; only the
*transcribe this utterance* step is now swappable
(`stt_providers/`, one file per provider + a `REGISTRY` dict in `__init__.py`
— add a fourth provider by adding one file, nothing else changes). Set in
`voice_params.yaml`:

```yaml
stt_provider: local          # local | sarvam | soniox
stt_source_language: te      # Telugu
stt_target_language: en
```

**Why this exists:** the household speaks Telugu-English code-switched, and
generic Whisper is measurably bad at that specific case — a benchmark on
code-switched speech found Whisper Large-v3's error rate jumps from 7–28%
CER on same-script language pairs to **32–51% CER on different-script pairs**
(exactly Telugu↔English). Sarvam's Saaras v3 and Soniox both offer direct
speech→English *translation* (not just transcription) trained for exactly
this. Cost/latency comparison (2026-09-04, see chat log for the full
numbers): Soniox is ~3–15x cheaper per audio-hour ($0.10–0.12/hr STT vs
Sarvam's ~$1.7–1.8/hr equivalent); Sarvam has lower published latency
(<150ms time-to-first-token vs Soniox's 260ms median) and is purpose-trained
on code-mixed Indian speech specifically, which is the more likely deciding
factor at household usage volumes (~$8/mo vs ~$36/mo either way — both
trivial).

| Provider | How it's called | Endpoint |
|---|---|---|
| `local` | in-process `faster-whisper` | — |
| `sarvam` | REST, one POST per utterance (matches how stt_node already batches) | `POST api.sarvam.ai/speech-to-text`, `mode=translate` |
| `soniox` | WebSocket, opened fresh per utterance (not kept alive across utterances — simpler, costs the connection handshake per utterance) | `wss://stt-rt.soniox.com/transcribe-websocket` |

**Degrade behavior (CLAUDE.md #4 — missing keys degrade, never crash):**
`SARVAM_API_KEY` / `SONIOX_API_KEY` live in `~/ros2_ws/.env` (placeholders in
`example.env`, loaded via `load_dotenv` exactly like `agent_node` does).
Missing key at startup, or *any* failure at call time (network, timeout, bad
response) → logs a warning and falls back to `local` for that utterance.
Verified live (no keys set): both `sarvam` and `soniox` selections start
clean and log `<provider> unavailable at startup (... not set); using local`
— confirmed the node never crashes or goes silent over this.

**⚠️ Not yet verified against a real key.** Sarvam's REST shape (multipart
file upload, `{"transcript": "..."}` response) is simple and was fetched
straight from the current API docs — reasonably high confidence. Soniox's
WebSocket path (batch-send: open, send config+audio+`""`, collect
`translation_status=="translation"` tokens, close) is transcribed from docs
without a live session — the token-joining logic in particular
(`''.join` vs `' '.join`, whether tokens carry their own spacing) needs
sanity-checking against real output before trusting it. Get a key, flip
`stt_provider`, say something in Telugu, check `/voice/user_input`.

---

## Measured performance (this Pi5, Cortex-A76 @ 2.4GHz, 4 cores, 2026-09-04)

| | RTF | note |
|---|---|---|
| Kokoro TTS, fp32 | **~1.8** | slower than real time, but the wire protocol already sends one sentence per message, so each reply sentence pays its own ~1.8x delay rather than compounding |
| Kokoro TTS, int8 quantized | ~3.75 | **worse**, not better — ARM NEON has no fast int8 path for this op set, unlike x86 VNNI. Use fp32. |
| faster-whisper STT, base/int8 | **~0.75** | faster than real time — ctranslate2's int8 path IS well-optimized for ARM, opposite of Kokoro's ONNX ops |

RTF = synthesis-or-transcription time ÷ audio duration; <1.0 is faster than
real time. The asymmetry (int8 helps whisper, hurts kokoro) is
counterintuitive and specific to this hardware — don't assume it generalizes,
re-measure before changing either model size.

Both mic and speaker paths were verified live: `aplay`/`arecord` round-trip
through the Blackwire (card 2) confirmed non-zero captured signal and audible
playback before any ROS code was involved.

---

## Status — what's verified, what isn't

✅ **TTS, fully verified end-to-end.** Published real sentences on
`/voice/robot_speech` through the actual node — audible output, correct
`/voice/tts_speaking` true→false transition around `<|eou|>`.

✅ **STT reached a real human voice once, genuinely end-to-end.** "Rakhi,
ఇవాళ టైమ్ ఎంత" spoken into the actual headset → VAD → (Sarvam timed out) →
local-fallback translate → "what is the time today" → `/voice/user_input` →
`agent_node` picked it up and started a turn. This is the one and only real
(non-bounced-audio) test run so far, and it worked. See TODO.md for what
happened on every attempt after it.

🟡 **STT is currently unreliable on repeat attempts — open, unexplained.**
After that first success, no further utterance (several tries) has
triggered VAD at all, despite `arecord` on the same hardware confirming real
signal reaches the mic every time. Not yet root-caused; TODO.md has the
elimination steps taken so far (raw capture fine, `pw-record` tool itself is
a red herring, the threading fix below didn't fix it either) and the next
diagnostic steps. **Don't treat this as "STT works" yet — treat it as "STT
worked once and needs its flakiness explained before it's trustworthy."**

🐛 **Found and fixed: `_transcribe()` was blocking the audio callback
thread.** The one live Sarvam attempt timed out after 8s — that 8s ran
*inside* the sounddevice/PortAudio callback, which can stall or corrupt the
input stream if a callback doesn't return promptly. Fixed (commit
`3f3395b`): `_on_audio` now only does VAD + framing; a background worker
thread does the actual `transcribe()` call. Confirmed this wasn't the whole
story, though — the VAD-silence issue above recurred on a freshly-launched
node after the fix.

❌ **Sarvam's own success path — still unconfirmed.** The only live attempt
against Sarvam timed out; the correctness of a real 2xx response was never
observed, only the fallback path. Soniox has never been reached at all (no
key yet).

---

## Running it

```bash
# Never run this alongside the Jetson's ai_stack voice role — both publish
# /voice/robot_speech consumers and /voice/user_input producers; you'd get
# double-speak and double-transcribe. Check first:
ssh jetson 'fleet_role.sh voice status'

cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch pi5_voice_pkg voice_launch.py
```

Params: `src/pi5_voice_pkg/config/voice_params.yaml` (model paths, voice,
wake aliases, stop words, VAD aggressiveness). Model weights live in
`src/langrobo_ros/models/` — gitignored (too large), see that directory's
`README.md` to refetch.

Not yet wired into `fleet.sh` or systemd — currently a manual `ros2 launch`.
Once the live-mic test passes, promoting this to a `fleet.sh rover --voice`
flag (or its own systemd unit, mirroring `langrobo-brain`) is the natural
next step.
