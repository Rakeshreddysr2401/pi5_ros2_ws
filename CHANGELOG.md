# Changelog

What changed, when, and where. Deployment steps for pending items: DEPLOY.md.

---

## 2026-07-03 — KV-cache fixes: warm turns every time (20s → ~0s prefill)

Latency replay showed cache-miss turns re-prefilling the full ~2.1k-token chat
prompt on the Mac Mini 12B at ~105 tok/s ≈ 18–20s. Two root causes, both fixed:

- **Slot scatter** — the llama-server runs `--parallel 4`; unpinned sequential
  requests can land on different slots, each a cold KV cache. `llm.py` gained a
  global `slot` config (new `llm_slot` param, default 0) so every text agent's
  request carries `id_slot: 0`; `local_agent_slot` default changed -1 → 1 so
  vision turns get their own slot instead of evicting the text prefix.
  (Per-agent override plumbing already existed; only the global pin was new.)
- **Per-minute clock in the prompt** — chat's `== NOW ==` line (minute
  resolution) made consecutive turns diverge mid-prompt; measured on the robot
  server, that reliably produced a FULL re-prefill (the fork's cache matcher
  found zero reuse), i.e. a 20s turn on every wall-clock minute tick. The
  prompt now carries only the date (changes once/day); the exact clock moved
  to a new `get_current_time` chat tool (`tools/system.py`). "What time is
  it?" costs one extra warm LLM roundtrip (~2s) instead of taxing every turn.
- Verified via `scripts/latency_replay.py` + the server's `/slots` counters:
  turns across minute boundaries now show `n_prompt_tokens_processed` ≈ new
  tokens only (llm 4.2s for a 39-token reply = pure decode; was 20.6s).
  Remaining first-audio latency is decode-bound (~9.5 tok/s on the 12B) —
  model choice, not caching.
- Diagnosis artifacts: request-capture proxy + slot A/B tests (scratchpad,
  not committed). Observed unattributed ~42-token requests occasionally landing
  on slot 0 — worth identifying if cold turns reappear.

## 2026-07-03 — Recurring reminders (closes the last phase-3 gap)

"Every day at 9pm remind me to take my medicine."

- `Reminder` gained `repeat_minutes` (0 = one-shot; 1440 daily, 10080 weekly,
  min 5). One-shots are removed when fired; repeating ones reschedule to the
  next future occurrence — a long brain downtime produces ONE catch-up
  announcement, never a backlog. Announcements snapshot the original due time.
- `set_reminder` / `list_reminders` show the cadence ("repeats daily");
  cancelling a repeating reminder removes it for good. Old-format JSON stores
  load unchanged (field defaults to one-shot).
- Verified live: the LLM maps "every day at 9pm…" → at_time="21:00",
  repeat_minutes=1440. Unit tests cover rescheduling, catch-up, back-compat.

## 2026-07-03 — Household lists & memory (completes roadmap phase 3 software)

"Add milk to the shopping list", "remember that the spare key is in the blue
drawer", "where's the spare key?" — persistent, local, no cloud.

- **`graph/tools/household.py`** (new, pure zone): `HouseholdStore` (one JSON
  file, `~/.langrobo/household.json`, atomic writes) holding named lists +
  free-form facts. Chat tools: `update_list(name, add, remove, clear)` (single
  tool for all list ops — keeps chat's tool count small-model-friendly),
  `remember(fact)`, `forget(about)`.
- **Recall design — deliberately no vector RAG / Mem0**: a household corpus is
  a few hundred short facts; `household_context()` injects the whole compact
  block into chat's system prompt, so reads need no tool call and recall can't
  silently miss. Bounds: 150 facts × 200 chars, 60 items/list, "memory almost
  full" nudge. Block sits between the static prompt and the time line, so the
  llama.cpp prefix cache only re-prefills when memory changes. Upgrade path
  (phase 4 per-person memory): llama.cpp `/v1/embeddings` + local index.
- Verified live off-robot: add → remember → recall-from-prompt → conversational
  removal ("we bought the milk") all correct, store state checked each turn.

## 2026-07-03 — Wake word "Rakhi" (software half of roadmap phase 2)

The robot stops answering every utterance in the room — the single biggest
daily-usability fix available without new hardware.

- **Jetson `wake_gate.py`** (new, pure/unit-tested): forward an utterance only
  if it starts or ends with a wake alias (alias stripped before forwarding;
  bare "Rakhi?" forwards as-is so the robot responds to being called), or the
  attention window is open. Alias list covers Whisper's spellings of "Rakhi".
- **Jetson `stt_node`**: applies the gate after transcription; a 15s attention
  window opens whenever the robot finishes speaking or is addressed, so
  follow-ups need no name. Ignored chatter is logged with its transcript
  (+ `wake_ignored` timing event) for alias tuning. `wake_word` /
  `wake_aliases` / `attention_s` params; `wake_word:=false` disables.
- Brain untouched — proactive `[SYSTEM]` turns (reminders) speak regardless
  and open the attention window via the robot's own speech.
- On-robot: tune `wake_aliases` from the ignored-transcript logs (DEPLOY.md).

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
