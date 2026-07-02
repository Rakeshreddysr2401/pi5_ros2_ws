> **Start at STATUS.md** — the done/next/priorities dashboard. This file holds
> the current phase's working notes and decisions.

# Goal

Make a home robot that can see, listen, speak and interact with its environment.
Add features that make people's general life easier. Must work effectively without
much delay, with clean, modular code and best-in-class agentic flows (easy handover
between ROS topics and LangGraph).

**Limitations:** think only in the range of what is currently possible with existing
resources (Jetson Orin Nano 8GB, Pi5 8GB, ESP32, speaker, mic, Mac Mini for LLM
inference, Logi Brio 100 webcam). Nav/SLAM/nvblox wait for the depth camera —
development doesn't stop until it arrives.

**Repos:** this repo = Pi5 brain. Jetson repo = `/Users/rakeshreddy/PycharmProjects/speech_vision`.

---

# Current Phase: Harden the Core Voice Loop

Decided 2026-07-02 (grill-me interview). Priorities before any new feature or refactor.

**Target: ≤ 2 seconds** from end of user speech to first robot audio (simple chat
turn, no tools). Whole pipeline in scope — both repos.

## Decisions

| Decision | Choice |
|---|---|
| Streaming TTS | Yes — brain streams sentence chunks to `/voice/robot_speech`; Jetson `tts_node` queues and plays in order; end-of-utterance marker; String topic unchanged |
| Routing hop | Fresh turns default to `chat` (one LLM call in the common case); supervisor kept for `[SYSTEM]` events and handback disambiguation |
| Persona | Shared identity block in every agent prompt: **"You are Rakhi, a home robot built by Rakesh."** Fixes the "developed by Google" leak (see Issues/) |
| Barge-in | No — keep half-duplex mic gating (`/voice/tts_speaking`); fast-follow: "stop" keyword spotter; full echo-cancelled barge-in is a later phase |
| Verification | Stage-timing events on `/diag/timing` + replay harness injecting canned utterances; per-stage waterfall, before/after numbers for every optimization |

## Work Order (measure after every step)

1. ✅ **Instrumentation + replay harness** — `/diag/timing` events + `scripts/latency_replay.py`
   - Stages: `stt_end` → `brain_receive` → `llm_start`/`llm_end` per agent → `speech_publish` → `tts_receive` → `tts_audio_start`
   - TODO: rebuild `voice_pkg` on the Orin, run harness on Pi5 for true baseline
2. ✅ **Persona block** — `graph/persona.py` in all 6 user-facing agent prompts (verified)
3. ✅ **Default-to-chat routing** — fresh turns enter chat; verified 1 LLM call for general turns
4. ✅ **Streaming TTS** — implemented 2026-07-03, both repos
   - Protocol: sentence chunks on `/voice/robot_speech` (String unchanged) +
     `<|eou|>` end-of-utterance marker; Jetson holds `/voice/tts_speaking` True
     across chunk gaps (8s `eou_timeout` watchdog if the marker is lost)
   - Brain: `graph/utils/speech_stream.py` (splitter + callback handler),
     `ChatOpenAI(streaming=True)` behind `stream_speech` param (rollback:
     `stream_speech:=false`); supervisor stays non-streaming; Pi5 speech queue +
     300ms drain timer removed (publish immediately, Jetson orders)
   - Pre-tool text now streams = natural "let me check…" acknowledgement
   - Verified off-robot vs Mac Mini llama.cpp: sentences stream mid-generation,
     tool calls parse in stream mode with NO raw JSON leaking to speech, final
     text dedup works (marker-only close)
   - TODO on-robot: rebuild `voice_pkg` on the Orin (tts_node changed), rebuild
     `ai_agent` on Pi5, then `scripts/latency_replay.py` for before/after — the
     metric is now first-sentence audio, and the harness anchors on FIRST
     tts_audio_start
5. ✅ **"Stop" keyword spotter** — implemented 2026-07-03, needs on-robot tuning
   - While TTS plays, stt_node transcribes ONLY short isolated bursts (0.2–1.5s;
     longer = robot's own voice, discarded pre-Whisper) and publishes
     `/voice/tts_stop` when the transcript is ≤3 words containing "stop"
   - tts_node: halts playback mid-chunk (interruptible Kokoro backend), flushes
     the queue, swallows in-flight chunks until the brain's `<|eou|>`; self-echo
     guard skips stop signals while the playing chunk contains "stop"
   - Pi5 brain: `/voice/tts_stop` also cancels nav + motion (safety word)
   - NOT verifiable off-robot (mic+speaker acoustics) — tune `_SPOT_*` constants
     and check false-trigger rate on the Orin; `stop_spotter:=false` disables

## Doc debt found along the way

- ✅ ARCHITECTURE.md `speak()` references — cleaned up with the streaming-TTS pass.
- ✅ `graph/prompts.py` speak() mentions — fixed (file stays reference-only).

---

# Next Phase Candidates (after core loop is fast and clean)

Highest-value features the current infra genuinely supports:

- ✅ Timers / reminders with proactive speech — done 2026-07-03. First
  self-initiated turn path: `graph/tools/reminders.py` (persistent store +
  chat tools) + 5s poll in agent_node injecting `[SYSTEM] Reminder due` turns
  (system queue is now FIFO — events no longer clobber each other). Verified
  live off-robot: set → list → due → spoken announcement. Ships with the same
  Pi5 rebuild as streaming TTS (see DEPLOY.md).
- ✅ Wake word ("Rakhi"), software half — done 2026-07-03. Transcript-based
  gate in stt_node (`voice_pkg/wake_gate.py`, pure + unit-tested): utterances
  must start/end with a wake alias or fall in the 15s attention window after
  the robot spoke; alias stripped before forwarding; ignored chatter logged
  (+ `wake_ignored` timing event) for alias tuning. `wake_word:=false`
  disables. Hardware half (far-field mic array for across-the-room pickup)
  still pending purchase.
- Face recognition + per-person memory (Jetson has headroom)
- Full barge-in (needs acoustic echo cancellation on Jetson)
- Depth camera arrives → nav, SLAM, nvblox, `/vision/find_object_pose`


---

# Product Direction

See **PRODUCT.md** — LangRobo product thesis ("private household member"),
ranked daily-use features, small hardware buys (mic array first, no wheels
until depth cam), and the 7-phase roadmap. Key architectural gap after this
phase: self-initiated turns (scheduler + event producers → `[SYSTEM]` turns).