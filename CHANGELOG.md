# Changelog

What changed, when, and where. Deployment steps for pending items: DEPLOY.md.

---

## 2026-07-03 — "Stop" keyword spotter (phase step 5) — needs on-robot tuning

Halt speech (and wheels) mid-reply without open-mic barge-in or AEC.

- **Jetson `stt_node`**: while `/voice/tts_speaking` is True the mic no longer
  goes fully deaf — short isolated bursts (0.2–1.5s) are transcribed and a
  ≤3-word transcript containing "stop" publishes `/voice/tts_stop`. Continuous
  speech (the robot's own voice) is discarded by the duration cap before ever
  reaching Whisper. `stop_spotter` / `stop_keyword` params; transcription queue
  now carries tagged (user|spot) segments.
- **Jetson `tts_node`**: on stop — abort current playback, flush the queue,
  swallow in-flight chunks until the brain's `<|eou|>` (without eating the next
  utterance), self-echo guard when the playing chunk itself contains "stop".
- **Jetson `tts_backend`**: `stop()` on the backend API; Kokoro playback is now
  interruptible on both paths (sd.stop() / pw-cat Popen kill).
- **Pi5 `agent_node`**: subscribes `/voice/tts_stop` → cancels navigation and
  interrupts motion tools — "stop" is a whole-robot safety word.
- Acoustic behaviour is untestable off-robot: verify per DEPLOY.md, tune
  `_SPOT_*` constants; `stop_spotter:=false` disables cleanly.

## 2026-07-03 — Reminders & timers with proactive speech

First self-initiated speech path (PRODUCT.md roadmap phase 3, partial).

- **`graph/tools/reminders.py`** (new, pure zone): thread-safe `ReminderStore`
  persisted to `~/.langrobo/reminders.json` (survives restarts, atomic writes)
  + chat tools `set_reminder(text, in_minutes|at_time, day)`, `list_reminders()`,
  `cancel_reminder(id)`. Timers are short reminders.
- **agent_node**: 5s poll pops due reminders and injects
  `[SYSTEM] Reminder due — announce to the user now: …` → supervisor → chat →
  spoken announcement. The system-event slot became a **FIFO queue** — a
  reminder can no longer clobber a nav-done or delivery event (latent bug).
- **chat**: owns reminders (prompt guidelines + examples); system prompt now
  ends with current date/time (appended last to keep the llama.cpp prefix
  cache warm) — also makes "what time is it" answerable.
- Verified live off-robot (real LLM, StubBridge): set → list → due →
  unprompted streamed announcement. Unit tests: store persistence, validation,
  due-popping, past-time-rolls-to-tomorrow.

## 2026-07-03 — Streaming TTS (phase step 4) — deploy pending, see DEPLOY.md

Reply audio starts after the **first sentence** instead of the full generation.

- **Protocol** (`/voice/robot_speech`, String unchanged): 1..N sentence-chunk
  messages + one `<|eou|>` end-of-utterance marker. Jetson holds
  `/voice/tts_speaking` True across chunk gaps (no mic unmute mid-reply),
  8s `eou_timeout` watchdog if the marker is lost.
- **Brain**: new `graph/utils/speech_stream.py` — sentence splitter
  (abbreviations, decimals, newlines, 250-char overflow guard) + LangChain
  callback handler publishing sentences as tokens arrive.
  `ChatOpenAI(streaming=True, stream_usage=True)` behind new `stream_speech`
  param (default true; false = full-reply publishes, protocol unchanged).
  Supervisor stays non-streaming. Pi5 speech queue + 300ms drain timer
  **removed** — chunks publish immediately, Jetson orders playback.
- **Jetson `tts_node`**: marker handling, queue 3→64 (no more drop-oldest,
  which would have eaten mid-utterance chunks), speaking-state per utterance.
- Pre-tool text now streams = natural "let me check…" acknowledgement (chat
  prompt allows one short ack with tavily_search).
- **Latency harness**: end-to-end now anchors at FIRST `tts_audio_start`.
- Verified off-robot vs Mac Mini llama.cpp: mid-generation sentence streaming;
  tool calls parse in stream mode with no raw-JSON leakage; no double-speak.
- Doc debt cleared: stale `speak()` references in ARCHITECTURE.md / prompts.py.

## 2026-07-03 — Product direction

- **PRODUCT.md** (new): LangRobo thesis — "private household member";
  local-first privacy as the moat; daily-use features ranked; small hardware
  buys (far-field mic array first, ~$80–125 total, no motors/tyres until the
  depth camera); 7-phase roadmap; two-week unprompted family retention as the
  success metric.
