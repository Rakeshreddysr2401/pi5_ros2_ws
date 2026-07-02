# Deployment Checklists

Pending rollouts and how to verify them on the robot. Newest first.

---

## "Stop" keyword spotter (implemented 2026-07-03 — ships with the same rebuilds)

Both repos again (stt_node, tts_node, tts_backend on the Jetson; agent_node on
the Pi5). While the robot speaks, the mic listens only for short bursts and
"stop" halts speech mid-sentence — and the Pi5 cancels any motion (safety word).

### Verify (acoustics can only be tested on the robot)

- Ask for something long ("tell me a story"), then say **"stop!"** firmly while
  it talks → speech halts within ~1s, robot goes quiet, mic unmutes normally,
  next question works.
- Let it speak several long replies WITHOUT saying stop → no false halts
  (self-echo + duration filters working). If it self-stops, check the Orin log
  for `Stop keyword spotted` transcripts to see what triggered it.
- While it drives AND talks, say "stop" → wheels and voice both stop.
- Tuning knobs: `_SPOT_*` constants in stt_node.py (burst window), the
  `stop_keyword` params, GPU load from spot transcriptions (each ≤1.5s clip is
  one small Whisper call, only during playback). Kill switch:
  `stop_spotter:=false` on the voice launch.

---

## Reminders & timers (implemented 2026-07-03 — ships with the same Pi5 rebuild)

Brain-only change (no Jetson rebuild). Chat owns `set_reminder` /
`list_reminders` / `cancel_reminder`; a 5s poll in agent_node injects
`[SYSTEM] Reminder due` turns → the robot announces them unprompted. Store
persists at `~/.langrobo/reminders.json` on the Pi5.

### Verify (after the Pi5 rebuild below)

- "Rakhi, remind me to check the oven in two minutes" → confirmation, then an
  unprompted spoken reminder ~2 min later (±5s poll).
- "what reminders do I have" / "cancel reminder one".
- "set a timer for one minute" → announcement when it fires.
- Restart the brain with a pending reminder — it must still fire (persistence).

---

## Streaming TTS (implemented 2026-07-03 — NOT yet deployed on robot)

Both repos changed. The brain now streams replies to `/voice/robot_speech` as
sentence chunks ending with a `<|eou|>` marker message; the Jetson holds
`/voice/tts_speaking` True across chunk gaps. Protocol details: INTEGRATION.md
topic table; implementation: `graph/utils/speech_stream.py` (Pi5) and
`voice_pkg/tts_node.py` (Jetson).

**Deploy BOTH sides together.** Old tts_node + new brain speaks the literal
"<|eou|>" text; new tts_node + old brain trips the 8s mic-release watchdog
after every reply.

### 1. Jetson (Orin)

```bash
cd ~/speech_vision/ai_ws        # pull latest first
colcon build --packages-select voice_pkg
source install/setup.bash       # then restart voice launch
```

### 2. Pi5

```bash
cd ~/pi5_ros2_ws                # pull latest first
colcon build --packages-select ai_agent
source install/setup.bash       # then restart brain launch
```

### 3. Verify

```bash
# On the Pi5 (agent_node + Jetson voice stack running):
python3 scripts/latency_replay.py "hello robot" "tell me a fun fact about space"
```

- End-to-end is now measured to the **first** `tts_audio_start` (first sentence
  audio) — this is the number to compare against the 2.0s budget and the
  pre-streaming baseline.
- Waterfall should show interleaved per-chunk `speech_publish → tts_receive →
  tts_synth_start → tts_audio_start → tts_end`, then `speech_eou →
  tts_utterance_end`.
- Talk to it live: multi-sentence replies must not unmute the mic between
  sentences (no self-hearing), and the mic must unmute promptly after the
  reply ends.
- Ask something needing web search ("what's the weather?"): you should hear a
  short acknowledgement while the search runs, and NEVER raw JSON.

### Watch out for

- **Raw JSON spoken aloud** → the Jetson/Pi5-facing llama.cpp build doesn't
  stream tool calls. Update llama.cpp (needs `--jinja`) or roll back.
- **Mic stuck muted ~8s after replies** → tts_node never saw `<|eou|>`; the
  brain side is old code. Rebuild ai_agent on the Pi5.
- **Choppy delivery on very short sentences** — Kokoro synthesises per chunk;
  if gaps are audible, raising `_MIN_CHUNK_CHARS` in
  `graph/utils/speech_stream.py` merges short sentences.

### Rollback

```bash
ros2 launch robot_brain brain_launch.py stream_speech:=false
```

One full reply per turn again (protocol unchanged — the marker still closes
each utterance, so the Jetson side needs no rollback).

Verified off-robot 2026-07-03 vs the Mac Mini llama.cpp (Gemma 4 12B):
sentences stream mid-generation, tool calls parse in stream mode with no JSON
leakage, no double-speak.
