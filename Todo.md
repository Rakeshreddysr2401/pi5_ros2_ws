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
4. **Streaming TTS** — cross-repo, biggest perceived-latency win; streamed pre-tool
   text becomes the natural "let me check…" acknowledgement (do after on-robot baseline)
5. **"Stop" keyword spotter** — halt TTS without open-mic barge-in

## Doc debt found along the way

- ARCHITECTURE.md still documents `tools/speech.py` / `speak()` — removed; single
  TTS channel is the final message text.
- `graph/prompts.py` (reference-only) still mentions `speak()`.

---

# Next Phase Candidates (after core loop is fast and clean)

Highest-value features the current infra genuinely supports:

- Wake word ("Rakhi") — half-duplex today makes this natural
- Face recognition + per-person memory (Jetson has headroom)
- Timers / reminders with proactive speech
- Full barge-in (needs acoustic echo cancellation on Jetson)
- Depth camera arrives → nav, SLAM, nvblox, `/vision/find_object_pose`
