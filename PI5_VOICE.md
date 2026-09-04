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

✅ **STT pipeline logic verified.** VAD triggers on real audio, whisper
transcribes, the confidence filter and wake-alias gate both correctly
*reject* bad input — tested against ambient noise (silently dropped) and a
Whisper hallucination on weak/reverberant round-trip audio ("Thank you very
much." — the exact failure mode VOICE_QUALITY.md documents for a mic that
isn't close-talk). No false publish to `/voice/user_input` in either case.

❌ **True wake-word recognition — not yet tested.** Every STT test so far
was a speaker bounced across a room into a close-talk boom mic (the Blackwire
is built for a mouth a few cm away, not room pickup) — an artificial,
worst-case test that can't validate a real "Rakhi, ..." said close to the
mic. See TODO.md.

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
