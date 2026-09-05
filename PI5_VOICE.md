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
*transcribe this utterance* step is now swappable. A provider is one file in
`stt_providers/` (subclass `STTProvider`, implement `transcribe()` +
`from_config()`) plus one line in the `REGISTRY` dict — `stt_node` has **no
per-provider knowledge** (no name/env-var/constructor if-else), because
`from_config(params, env)` on each provider owns its own API-key env var and
param mapping. See "Adding a provider" below. Set in `voice_params.yaml`:

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

**✅ Sarvam verified against a real key (2026-09-04, evening).** With
`SARVAM_API_KEY` set and `stt_provider: sarvam`, live Telugu speech
translated to fluent English on `/voice/debug_transcript` across many
consecutive utterances (e.g. "రేపు సినిమాకి వెళ్దామా" → "Shall we go to the
movie tomorrow?", food/navigation phrases all clean). The multipart upload +
`{"transcript": "..."}` response shape is confirmed against the live API (a
direct `curl` probe returned HTTP 200 with the documented JSON).

**⚠️ Soniox still unverified.** No Soniox key yet. Its WebSocket path
(batch-send: open, send config+audio+`""`, collect
`translation_status=="translation"` tokens, close) is transcribed from docs
without a live session — the token-joining logic in particular
(`''.join` vs `' '.join`, whether tokens carry their own spacing) needs
sanity-checking against real output before trusting it. Get a key, flip
`stt_provider`, say something in Telugu, check `/voice/user_input`.

---

## TTS provider switch — local | Sarvam | Sarvam-translate | Soniox

Symmetric to STT: `tts_node`'s queueing / playback / `<|eou|>` protocol is
unchanged; only the *synthesize this sentence* step is swappable, same
one-file + `REGISTRY` + `from_config()` rule (see "Adding a provider"). Set in
`voice_params.yaml`:

```yaml
tts_provider: local          # local | sarvam | sarvam_translate | soniox
tts_language: en
tts_sarvam_voice: ritu       # bulbul:v3 speaker (sarvam + sarvam_translate)
tts_translate_from: en       # sarvam_translate only: source text language
tts_translate_to: te         # sarvam_translate only: spoken language
```

**Why cloud TTS:** local Kokoro fp32 is RTF ~1.8 (slower than real time);
Sarvam/Soniox are sub-second. This is a *latency* motivation — unlike STT,
whose motivation was Telugu translation quality.

| Provider | What it does | Calls per sentence |
|---|---|---|
| `local` | Kokoro-onnx, in-process, English voice | — |
| `sarvam` | Bulbul v3 TTS — speaks the text **as given** | `POST api.sarvam.ai/text-to-speech` |
| `sarvam_translate` | **English text → Telugu speech**: translate, then TTS | `POST /translate` + `POST /text-to-speech` |
| `soniox` | Soniox TTS v2, plain REST | `POST tts-rt.soniox.com/tts` |

**`sarvam_translate` — English reply, spoken in Telugu.** Plain TTS does not
translate: hand English text to a Telugu voice and you get mispronounced
English. This provider chains Sarvam's Text Translation (source→target text)
then Bulbul TTS of the translated text, so the robot is *heard* in Telugu while
the brain still replies in English. Target is configurable via
`tts_translate_from`/`tts_translate_to` (any Sarvam-supported Indic language,
not just Telugu). Costs two API calls per sentence (still under Kokoro's local
time), and degrades to local Kokoro (English) on any failure. Longer term,
having the brain reply in the target language directly (one source of truth,
one fewer call per sentence) is the alternative — this provider is the
pragmatic path and the right tool for testing today.

**Sample-rate gotcha (Sarvam + Soniox TTS):** output is pinned to **24000 Hz**.
Sarvam's unstated default is 22050, which the Blackwire headset's ALSA output
rejects (`Invalid sample rate`, PaErrorCode -9997); 24000 is the rate Kokoro
already plays successfully. Don't change it without confirming the headset
accepts the new rate.

### Adding a provider (STT or TTS)

Two steps, and the node files are never touched:

1. **One file** in `stt_providers/` (or `tts_providers/`). Subclass
   `STTProvider`/`TTSProvider` and implement:
   - `transcribe(pcm, sample_rate) -> str` (STT) or
     `synthesize(text) -> (samples, sample_rate)` (TTS) — raise
     `ProviderUnavailable` on any failure that should fall back to local.
   - `@classmethod from_config(cls, params, env) -> Provider` — read this
     provider's API key from `env` (its own `*_API_KEY` name) and its
     settings from `params`; raise `ProviderUnavailable('… not set')` if the
     key is missing. This is the only place a provider's env-var/constructor
     lives.
2. **One line** in that folder's `__init__.py` `REGISTRY` dict:
   `'myprovider': MyProvider`.

Then `stt_provider: myprovider` (or `tts_provider:`) in `voice_params.yaml`.
`params` is the dict the node assembles from its ROS params (STT: model_size/
model_dir/threads/source_language/target_language; TTS: model_path/voices_path/
voice/speed/threads/language/sarvam_voice/soniox_voice/translate_from/
translate_to) — add a `declare_parameter` in the node only if your provider
needs a genuinely new setting. Missing key or any runtime failure
auto-falls-back to local; you never have to handle that in the provider beyond
raising `ProviderUnavailable`.

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

✅ **TTS verified end-to-end, all three provider modes (2026-09-04, evening).**
Real sentences published on `/voice/robot_speech` through the actual node,
audible output, correct `/voice/tts_speaking` true→false around `<|eou|>`:
- `local` (Kokoro): clean English audio, confirmed live.
- `sarvam` (Bulbul v3): API confirmed HTTP 200 returning a valid base64 WAV
  (`ritu` speaker), and spoken live.
- `sarvam_translate` (**English text → Telugu speech**): full chain confirmed —
  "The robot is charging now…" → Telugu text → 4.5 s of 24 kHz audio, played
  audibly through the headset. Both the `/translate` and `/text-to-speech`
  calls return HTTP 200 with the documented shapes.

✅ **STT verified end-to-end, repeatedly (2026-09-04, evening).** Both paths
ran clean across many consecutive utterances with no drop-out:
- `local` English→English: plain speech transcribed correctly, back to back.
- `sarvam` Telugu→English: live Telugu translated to fluent English on
  `/voice/debug_transcript` (movie/food/navigation phrases all clean).
The earlier "worked once then VAD went silent" flakiness **did not
reproduce**; the node stayed alive and transcribing throughout. Whatever that
was, it is not currently happening — see the resolved note below.

🟩 **Resolved (could not reproduce): the VAD-silence flakiness.** The morning
run saw one success then several attempts with no VAD trigger at all. In the
evening run — after moving `transcribe()` off the audio-callback thread
(commit `3f3395b`) and adding `[diag]` logging (audio-callback heartbeat +
`voiced` state, and ALSA `status` warnings promoted from `.debug()`) — the
callback fired continuously and VAD tracked speech normally over a long
session. Leading theory for the original symptom: the morning's in-callback
8s Sarvam timeout stalled/corrupted the PortAudio stream and any ALSA input
overflow was swallowed at `.debug()`. The `[diag]` lines stay in until we've
logged a few more clean multi-day sessions, then remove per TODO.md.

🐛 **Found and fixed: `_transcribe()` was blocking the audio callback
thread.** The morning Sarvam attempt timed out after 8s — that 8s ran
*inside* the sounddevice/PortAudio callback, which can stall or corrupt the
input stream if a callback doesn't return promptly. Fixed (commit
`3f3395b`): `_on_audio` now only does VAD + framing; a background worker
thread does the actual `transcribe()` call.

✅ **Sarvam success path confirmed.** A real key now returns HTTP 200 with the
documented `{"transcript": "..."}` JSON (direct `curl` probe + live speech).
Soniox has still never been reached (no key yet).

---

## Running it

```bash
# Never run this alongside the Jetson's ai_stack voice role — both publish
# /voice/robot_speech consumers and /voice/user_input producers; you'd get
# double-speak and double-transcribe. Check first:
ssh jetson 'fleet_role.sh voice status'

cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch pi5_voice_pkg voice_launch.py       # both nodes, provider from config
```

Params: `src/pi5_voice_pkg/config/voice_params.yaml` (providers, model paths,
voice, wake aliases, stop words, VAD aggressiveness). Model weights live in
`src/langrobo_ros/models/` — gitignored (too large), see that directory's
`README.md` to refetch.

**Against `langgraph dev` instead of the brain.** In dev mode the graph runs
inside the `langgraph dev` server, which has no ROS side — `agent_node` is not
there to answer, so this pair would transcribe into silence. Use the bridge
launch instead; it starts these same two nodes plus `studio_voice_node`, which
carries the utterance to the server on `:2024` and republishes the reply here:

```bash
./scripts/dev_voice.sh                            # or: everything at once, incl. the graph server
ros2 launch langrobo_ros studio_voice_launch.py   # voice:=false if this pair is already up
```

Same `/voice/*` contract and the same STT/TTS nodes — only the brain end
differs. Runbook: OPERATIONS.md → "Voice in dev mode (Studio)".

Not yet wired into `fleet.sh` or systemd — currently a manual `ros2 launch`.
Once the live-mic test passes, promoting this to a `fleet.sh rover --voice`
flag (or its own systemd unit, mirroring `langrobo-brain`) is the natural
next step.

---

## Testing (helper scripts in the repo root)

Three convenience scripts run a single node with live logs, each taking an
optional provider override (so you don't hand-type long `ros2 run` lines — a
wrapped paste splits the `--params-file` arg and fails):

| Script | What it does |
|---|---|
| `./run_stt.sh [provider]` | STT node only. `./run_stt.sh` = local (English→English); `./run_stt.sh sarvam` = Telugu→English. |
| `./run_tts.sh [provider]` | TTS node only. `local` / `sarvam` / `sarvam_translate` / `soniox`. |
| `python3 tts_say.py` | Interactive: type a line → publishes to `/voice/robot_speech` (+ `<\|eou\|>`) so the TTS node speaks it. Needs ROS sourced first. |

### Test STT (speak → text)

```bash
# Terminal 1 — start STT (add 'sarvam' for Telugu→English):
./run_stt.sh                 # or: ./run_stt.sh sarvam

# Terminal 2 — watch the raw transcript for every utterance (no wake word needed):
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 topic echo --field data /voice/debug_transcript
```
Say **"what is the time today"** → text appears in Terminal 2. Say
**"Rakhi, what is the time"** → Terminal 1 also logs `addressed to me: '…'`
and publishes to `/voice/user_input` (what the brain consumes). The `[diag]`
heartbeat in Terminal 1 shows `voiced=True` while you speak; any ALSA
`input status` overflow prints as a WARNING.

### Test TTS (text → speech)

```bash
# Terminal 1 — start TTS in the mode you want:
./run_tts.sh                 # local Kokoro (English)
./run_tts.sh sarvam          # Sarvam Bulbul (English, cloud, faster)
./run_tts.sh sarvam_translate # English text spoken in Telugu

# Terminal 2 — type text to hear it:
source /opt/ros/jazzy/setup.bash && source install/setup.bash
python3 tts_say.py
# say> Good morning. Breakfast is ready.
```

### Quick API-key check (no ROS, de-risks a cloud provider before a live test)

```bash
KEY=$(grep -E '^SARVAM_API_KEY=' .env | cut -d= -f2-)
# Sarvam STT (translate): record 2s, POST it, expect {"transcript": "..."}
arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d 2 /tmp/probe.wav
curl -s -H "api-subscription-key: $KEY" -F file=@/tmp/probe.wav \
  -F model=saaras:v3 -F mode=translate -F language_code=te-IN \
  https://api.sarvam.ai/speech-to-text
# Sarvam translate (en→te): expect {"translated_text": "..."}
curl -s -H "api-subscription-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"input":"dinner is ready","source_language_code":"en-IN","target_language_code":"te-IN","model":"sarvam-translate:v1"}' \
  https://api.sarvam.ai/translate
```

Debug topics for STT self-testing without SSH: `/voice/debug_vad` (fires the
instant VAD hands off an utterance — if you speak and see nothing here, the
problem is VAD/mic, not the provider) and `/voice/debug_transcript` (raw text
before the wake gate). The `[diag]` audio-callback logging in `stt_node` is
temporary — remove it once STT has logged a few clean multi-day sessions
(see TODO.md).
