# LangRobo — Status & Priorities

**The one file to open first.** What's done, what's next (by priority), and
where everything else is documented. Updated: 2026-07-03.

---

## ✅ Done (all code-complete 2026-07-02/03, verified off-robot where possible)

| # | What | Where | Verified |
|---|------|-------|----------|
| 1 | Latency instrumentation + replay harness (`/diag/timing`, per-turn waterfall) | both repos | ✅ off-robot |
| 2 | Persona "You are Rakhi, built by Rakesh" in all agent prompts | brain | ✅ |
| 3 | Default-to-chat routing — 1 LLM call for common turns (no supervisor hop) | brain | ✅ |
| 4 | **Streaming TTS** — audio starts after the first sentence; `<|eou|>` utterance protocol; mic muted across chunk gaps | both repos | ✅ off-robot vs Mac Mini LLM |
| 5 | **"Stop" keyword** — halts speech mid-sentence AND the wheels (safety word) | both repos | ⚠️ acoustics only testable on robot |
| 6 | **Wake word "Rakhi"** — only utterances addressed to the robot get answered; 15s follow-up attention window | Jetson | ✅ logic; ⚠️ aliases need on-robot tuning |
| 7 | **Reminders & timers** — incl. recurring ("every day at 9pm…"), proactive spoken announcements, persistent | brain | ✅ live LLM flow |
| 8 | **Household lists & memory** — shopping/todo lists, "remember/forget that…", in-prompt recall (no RAG needed at this scale) | brain | ✅ live LLM flow |
| 9 | Self-initiated `[SYSTEM]` turn pattern (FIFO event queue) — foundation for all proactive behaviour | brain | ✅ |
| 10 | Product thesis + roadmap ("private household member", local-first as the moat) | PRODUCT.md | — |

**Nothing above is deployed on the robot yet** — it all ships in one session (P0).

---

## 🎯 To do, by priority

### P0 — Robot deploy & verify session (blocks everything else)
One `colcon build` per machine, then walk DEPLOY.md top to bottom.
- Rebuild `voice_pkg` (Orin) + `ai_agent` (Pi5) — **both together** (protocol changed).
- `scripts/latency_replay.py` before/after — first-sentence audio vs the **≤2s budget**.
- Tune `wake_aliases` from ignored-transcript logs; check stop-spotter false triggers.
- Also check: Mac Mini is now serving Gemma 4 12B — confirm that's intended for
  the robot loop (12B prefill is slower than the 3n E4B the config assumed).

### P1 — Hardware order (~$80–125, do alongside P0)
- **Far-field mic array / USB conference speakerphone with AEC** — biggest UX
  lever; AEC also unlocks true barge-in later.
- Full-range speaker · pan-tilt servos + bracket for the Brio (ESP32-driven) ·
  LED state ring · mmWave presence sensor (LD2410).
- **Not** motors/tyres — mobility waits for the depth camera (see PRODUCT.md).

### P2 — Face recognition + per-person memory (roadmap phase 4; needs live camera)
- Jetson face_node (detect + embed + recognise) → `[SYSTEM] person seen` producer.
- Household memory store gains a `person` dimension → per-person greetings,
  briefings, "tell Rakesh when you see him".
- Fixes the wake-word trade-off: gaze/face = "is the user addressing me".
- This is also the trigger point to move memory recall from in-prompt to
  embeddings (llama.cpp `/v1/embeddings`) if facts outgrow the prompt.

### P3 — Embodied presence (after P1 parts arrive)
- Pan-tilt face tracking, LED listening/thinking/speaking states,
  presence-sensor `[SYSTEM]` producer (greet on entry, quiet hours).

### P4 — Visual household memory
- Periodic frame snapshots indexed via Gemma → "where did I leave my keys",
  "did I leave the stove on", door watch.

### P5 — Depth camera arrives → mobility with a job
- Nav2/SLAM/nvblox, patrol, come-when-called, follow-me.

### Cross-cutting (do opportunistically)
- **Usage logging** per feature → the two-week family-retention metric (PRODUCT.md).
- Clean `.env`: stale `LANGSMITH` key (403 spam every run), `SWIGGY_ACCESS_TOKEN` (401).
- Wake word: drop "rocky" alias if it false-triggers on real conversations.

---

## ⚠️ Known issues / trade-offs (accepted, documented)

- Wake-word attention window (15s) answers room chatter right after the robot
  speaks — fix lands with P2 (gaze attention).
- Stop spotter can miss a soft "stop" and can't work during the robot's own
  "stop"-containing sentences (self-echo guard) — real fix is AEC hardware (P1).
- Mac Mini cold prefill ~18s on the 12B model off-robot — watch on-robot numbers.

---

## 📚 Which file is what

| File | Purpose |
|------|---------|
| **STATUS.md** | This dashboard — done / next / priorities. Start here |
| PRODUCT.md | Product thesis, ranked daily-use features, hardware ROI, 7-phase roadmap |
| Todo.md | Current-phase working notes (hardening phase details, decisions table) |
| DEPLOY.md | Step-by-step rollout + verification checklists for the pending batch |
| CHANGELOG.md | Dated log of every change with file-level detail |
| ARCHITECTURE.md | How the brain works: graph, agents, tools, topics, threading |
| INTEGRATION.md | Cross-repo contract: full topic table incl. speech/wake/stop protocols |
| SETUP.md / README.md | Environment setup / repo intro |
| Issues/ | Raw captured transcripts of problem sessions |
| Jetson repo (`speech_vision`) | STT/TTS/vision nodes — voice_pkg changes ship from there |
