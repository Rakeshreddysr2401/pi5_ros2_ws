# Voice roadmap — from "it talks" to a Siri-grade assistant

**Status: planning. Written 2026-09-20.** This is the single tracking file for
the voice/response workstream. Phases are ordered by priority and dependency;
work them **one at a time, in order**, and do not start the next until the
current phase's exit test has passed **on the real robot** (CLAUDE.md: a rule
the model or the code has to follow is a thing to measure, not a thing to read).
Tick the boxes here as things land, and keep PI5_VOICE.md as the "how it works
today" doc — this file is "where it is going".

---

## 0. The target

What the robot must feel like, in the owner's words (2026-09-20): *"quality
similar to how Siri works — production grade, not messy."* Concretely:

| # | Behaviour | Depends on |
|---|---|---|
| T1 | Listens **only when called** by name — nothing is transcribed (or sent to a cloud STT) otherwise | Phase 1 |
| T2 | Acknowledges the call instantly ("haan boss?") when you pause after the name; stays silent if you keep talking | Phase 1 |
| T3 | **Never** hears or answers its own voice | Phase 0 (done) |
| T4 | Listens after the wake word, and again after each reply (follow-up window). **Owner clarified 2026-09-20: it does NOT need to hear you while it is talking** — "on wake word, or after it responds, it listens is my idea" | Phase 1 + 2 |
| T5 | Feels realtime: first audio well under 2 s on a warm turn, no double-speak, no dead air, no stuck mic | Phase 3 + 4 |
| T6 | Plays songs. (Hearing its name *over* the music is not possible on the Stone — see Phase 0b findings — so pausing needs Telegram/a button, or a device that keeps its mic open) | Phase 5 |
| T7 | Places a phone call ("call mom") through its own speaker and mic | Phase 6 (needs 0) |
| T8 | The **Bluetooth** speaker + mic (boAt Stone) is the audio device, and it just works — no reconnect ritual, no profile fights, no two nodes undoing each other's settings | Phase 0 |

**Hardware constraint (owner, 2026-09-20):** the Bluetooth speaker+mic stays.
A wired USB speakerphone is *not* the plan — every phase below is designed
around making the Bluetooth path conflict-free, not around replacing it.

## 1. Where it stands today (2026-09-20)

Measured from the code on `dev-1.3.3-minimal`, not from memory:

- **Wake word is OFF.** `voice_params.yaml`: `wake_detector: transcript_alias`,
  `require_wake: false` (commit `4bfbb08`, no rationale recorded). Every
  VAD-positive utterance in the room is sent to Sarvam and forwarded to the
  brain. The only real wake model is the bundled `hey_jarvis` stand-in, which
  scored 0.12 / 0.25 / 0.47 through the boAt Stone's HFP mic against a 0.35
  threshold — ~1 in 3 misses. No "Mitra" model has been trained.
- **Self-hearing is prevented by a hard mute.** `stt_node._on_audio` drops
  every frame while `/voice/tts_speaking` is true, plus `tts_tail_mute_s`
  (1.2 s). T3 holds, but the mic is dead while the robot talks, so **T4 is
  impossible as built** — even "stop" only works in the gaps between sentences.
- **"stop" is a cloud round-trip.** `stt_node._transcribe` sends the utterance
  to Sarvam, gets text back, then `_is_stop_command()` matches it. A safety
  word that costs ~1 s and an API call, and that is silent while the robot is
  speaking.
- **Barge-in exists on the brain side** (`agent_node._turn_interrupt`, checked
  at graph step boundaries) but: it can only be triggered while the mic is
  open, it waits for the current LLM/tool step to finish, and when it fires
  the sentences already queued in `tts_node` keep playing — the new answer
  lands after the abandoned one finishes talking.
- **Streaming TTS is solid.** `speech_stream.py` → `/voice/robot_speech` →
  `tts_node` (synth one sentence ahead of playback, generation counter for
  stops). Warm turn ~1.5 s to first audio; cold turn 50–108 s (KV cache /
  stale HTTP connection to the Mac Mini).
- **No music, no calling.** `/audio/music_*` was removed (no listener,
  CLAUDE.md Gotchas). Nothing in the repo dials anything.
- **Hardware:** boAt Stone 650 over Bluetooth **HFP** (8–16 kHz narrowband,
  chosen so one device is both speaker and mic). Whether the Stone does any
  echo cancellation of its own in HFP mode is **unknown** — never measured.
- **The Bluetooth glue has real conflicts today** (`bt_audio.py`):
  - Pairing does not survive a reboot — manual `bluetoothctl trust/pair/
    connect` + `pactl set-card-profile` before every launch.
  - **Both nodes run `bt_audio.ensure()` independently.** tts_node's HFP
    profile switch lands ~90 ms after stt_node's and re-creates the PipeWire
    source, wiping the mic gain stt_node just set (commit `218e8f7` papers
    over it with a one-shot timer). Two owners of one device.
  - PipeWire resets the mic level on every reconnect; the 4× software gain
    has to be re-applied each time (`f949265`).
  - The speaker sleeping / wandering out of range kills the output stream
    mid-utterance; the next sentence reopens it, the current one is lost, and
    the mic (which is the same device) drops with it — no reconnect logic.
  - No mSBC (16 kHz wideband HFP) check: if the Stone supports it, PipeWire
    may be negotiating 8 kHz CVSD, which is the worst case for both wake
    detection and STT.

---

## Phase 0 — Bluetooth audio done right (one owner + AEC) `[DONE 2026-09-20]`

**Why first:** T8 is the floor everything stands on, and T3/T4/T6/T7 all need
the mic live while the speaker is loud (echo cancellation) on *this* Bluetooth
device. Both halves shape the mic that every later phase is tuned against, so
they come before any wake-word tuning.

### 0a — One owner for the Bluetooth device

Today two nodes each run `bt_audio.ensure()` and race each other (see §1).
The fix is structural, not another timer:

- [x] **`audio_device_node`** (`pi5_voice_pkg/audio_device_node.py`) — the
  *only* thing that touches bluetoothctl / pactl / wpctl. Polls every 3 s:
  any paired-or-trusted Bluetooth audio device (owner's ask 2026-09-20: "not
  sure I always connect the boAt Stone, or some other headphones" — so
  `bt_devices` is a preference, whatever is switched on wins), re-pairs if
  the link key was lost, HFP when it has a mic, default sink + source, mic
  gain, re-applied whenever PipeWire re-creates the nodes. stt_node/tts_node
  no longer call `ensure()` (deleted) — they open `pipewire` and follow the
  topic.
- [x] `/voice/audio_ready` + `/voice/audio_device` (latched). stt_node closes/
  opens the mic on the edges; tts_node drops speech while false (EOU kept).
  `[ ]` still to do: surface it on the brain's `/status` endpoint.
- [x] **Reboot with no ritual**: `langrobo-voice` user unit + `run_voice.sh`,
  installed by `install_systemd.sh` (with `enable-linger`). `[ ]` not yet
  installed on this Pi — needs one sudo run of `./scripts/install_systemd.sh`.
- [ ] **Wideband HFP (mSBC).** Check `pactl list cards` for the Stone's
  supported codecs; if mSBC is offered, pin it (`bluez5.codecs` / WirePlumber
  rule) — 16 kHz instead of 8 kHz is a free win for wake detection and STT.
  Record which codec is actually negotiated in PI5_VOICE.md.
- [x] **Stream loss handling in tts_node.** On `/voice/audio_ready` false the
  open stream is closed and sentences are dropped (one warning per utterance)
  while the EOU still ends `tts_speaking` — the mic is never left muted.
- [x] PI5_VOICE.md's "after a reboot" recipe replaced by the owner-node section.

**Exit test (0a):** power-cycle the Pi5 with the Stone on → within 30 s of
boot `wpctl status` shows the Stone as default sink+source, `/voice/audio_ready`
is true, and a spoken sentence is transcribed with the 4× gain in effect —
zero commands typed. Then power-cycle the *speaker* while the robot is mid-
sentence → tts_speaking ends false, the mic reopens when the speaker returns,
the next turn works.

**Measured 2026-09-20, OnePlus Buds Z2 (not in `bt_devices` — proves the
"any headphones" rule):** trio started → node re-paired + connected + routed
the Buds unattended, mic opened, sentence spoken (Sarvam, 2.4 s synth, HFP
source at vol 4.00). Buds into the case → loss detected, mic closed, TTS
dropping — all within the same second. Buds back in pairing mode → re-paired,
connected, ready, mic reopened, speaker back in ~1 s; a second sentence played
cleanly. Zero commands typed. Boot half of the test still pending the unit
install (sudo).

### 0b — Echo cancellation on the Bluetooth path — MEASURED, NOT NEEDED

`scripts/aec_probe.py` (kept as the regression check for any audio-path
change): records the mic for 3 s of silence, then while the robot speaks a
real sentence through the real TTS path, then 3 s after; reports RMS, the
VAD "speech" ratio and the leak in dB; `--save` keeps the "robot talks"
segment so it can be transcribed with local Whisper.

**Measured 2026-09-20 (quiet room, mic gain 4×):**

| device (HFP) | silence rms | robot talks rms | VAD during | owner talking over it | verdict |
|---|---|---|---|---|---|
| boAt Stone 650 (CVSD 8 kHz) | 0.0073 | 0.0035 | 2% | **95–99% exact-zero samples**, Whisper hears nothing (at 100% and at 40% volume) | firmware **mutes its mic while it plays** (half-duplex). Zero echo; also zero owner. |
| OnePlus Buds Z2 (mSBC 16 kHz) | 0.0013 | 0.0012 | 3% | 6% zeros, −0.9 dB leak | real echo cancellation in the earbud |

Conclusions:
- **No software AEC.** `libpipewire-module-echo-cancel` is not enabled and
  nothing is inserted into the audio path — the owner's transcripts stay
  exactly what the device delivers ("previously something made me all wrong
  words" — this is why the measurement came first).
- The robot cannot hear itself on either device, so T3 holds by hardware.
  `stt_node`'s mute-while-speaking + `tts_tail_mute_s` stay as belt and
  braces (they cost nothing on a device that mutes anyway).
- On the Stone the robot **cannot hear anyone while it plays sound**. The
  owner's design does not need that (T4), so this is accepted, not fixed.
  It rules out voice barge-in mid-sentence and the wake word over music on
  the Stone; those need a device that keeps its mic open (the Buds do) or a
  non-voice pause (Telegram, a button).
- Stone negotiated CVSD (8 kHz). `[ ]` still worth checking whether it
  offers mSBC (`pactl list cards` while connected) and pinning it if so.

---

## Phase 1 — Wake word done properly, with an ack `[ ]`

**Why:** T1 and T2. This is the most visible "messy" behaviour today (the
robot transcribes the whole room and ships it to a cloud STT) and it is
independent of Phase 0 except for threshold tuning. Model training is offline
work and can start while Phase 0 is being measured.

**Tasks**
- [ ] **Train the real wake model.** openWakeWord custom model for "Mitra" (synthetic TTS-generated positives + the project's own recordings
  through the *actual* mic path; negatives from the room). Output
  `src/langrobo_ros/models/wake/mitra.onnx`, referenced by `wake_model_path`.
  Document the training recipe in VOICE_QUALITY.md §4 so it can be redone
  when the mic changes.
- [ ] **Re-enable acoustic gating:** `wake_detector: openwakeword`,
  `require_wake: true`. Re-tune `wake_threshold` from the `[diag] asleep
  peak_wake_score` log on the mic Phase 0 settled on. Keep `transcript_alias`
  as the documented fallback only.
- [ ] **Ack cue.** New additive topic `/voice/cue` (`std_msgs/String`:
  `wake` | `done` | `error`). `tts_node` maps each to a short audio clip
  pre-rendered **once at startup** with the configured provider (so "haan
  boss?" costs no API call per wake and sounds like the same voice) and plays
  it through the same output stream. Pause-aware: `stt_node` publishes `wake`
  only if no voiced frame arrives within ~400 ms of the wake firing — if the
  user keeps talking ("Mitra, go to the kitchen") the cue is skipped so it
  never talks over the command. Until Phase 2 lands, the cue must **not** set
  `/voice/tts_speaking` (it is < 0.5 s and muting would clip the command).
- [ ] **Follow-up window** stays (`follow_up_window_s`, restarted when the
  robot stops talking) — that is what makes "set a timer" / "five minutes"
  a conversation instead of two commands. Re-verify it with the cue in.
- [ ] The Jetson `speech_vision` tts ignores `/voice/cue` today; that is fine
  (additive, same precedent as `/voice/*_meta`). Note it in both CLAUDE.md
  files.
- [ ] **Pairing / switching by Telegram and by voice.** Owner's ask
  2026-09-20: manage the speaker from the Telegram chat — list what is paired,
  pick which to use, add a new one. Same tool serves both channels (Telegram
  has full agent access), and Telegram is the easier first step because it
  needs no working speaker to ask its questions — so build it Telegram-first,
  voice follows once the wake exit test passes. Dialogue: "connect my new
  earbuds" → "Put them in pairing mode and tell me when ready" → scan →
  "I found OnePlus Buds Z2, connect?" → "yes" → paired, trusted, routed.
  "Which speaker are you using?" / "use the Stone" for switching.
  Shape: `audio_device_node` grows two ROS services (`/voice/audio/scan`,
  `/voice/audio/pair`) wrapping the same `bt_audio` helpers the terminal flow
  uses; one owner-only tool on `chat` (`pair_bluetooth_device`) calls them via
  the bridge (stub.py mirrors it). Already-paired devices never need this —
  the node connects them on its own. The terminal version
  (`./scripts/bt_speaker.sh pair`, `scripts/bt_pair.py`) shipped 2026-09-20
  and is the reference for the dialogue; the Claude Code skill `/bt-audio`
  (`.claude/skills/bt-audio/SKILL.md`) is the operator's checklist.

**Exit test (real room, real mic):** 20 wake attempts at conversational
volume from 2 m → ≥ 18 fire; 30 minutes of TV/conversation without the name →
0 false wakes and **zero** `/voice/stt_meta` messages (nothing transcribed).
"Mitra" + pause → cue within 300 ms. "Mitra go forward" with no pause → no
cue, command reaches the brain intact (check the pre-roll is not clipped).

---

## Phase 2 — Conversation flow (listen after wake, listen after each reply) `[ ]`

**Why:** T4 as the owner actually wants it. Full-duplex barge-in was dropped
in Phase 0b (the Stone mutes its mic while playing; the design does not need
it). What is left is making the *turn-taking* feel right.

- [ ] **Follow-up window** (`follow_up_window_s`, restarted when the robot
  stops talking): tune the length on the robot with real conversations —
  long enough for "…and what about tomorrow?", short enough that the room's
  next unrelated sentence is not answered. Measure, don't guess.
- [ ] **"stop" fast and local when idle**: today it is a cloud STT
  round-trip (~1 s + an API call). A second openWakeWord model for
  "stop"/"aagu" makes a halt a ~50 ms local event. (While the robot is
  talking on the Stone nothing can be heard anyway; the wheels are stopped
  by any new utterance the moment the robot goes quiet.)
- [ ] **Abandoned turn must go quiet**: on `_turn_interrupt` `agent_node`
  publishes only the EOU today, so sentences already queued in `tts_node`
  keep playing under the new answer. Add `publish_speech_stop()` to the
  bridge (stub.py mirrors it) and call it on interrupt.
- [ ] `tts_tail_mute_s` (1.2 s): re-measure per device with `aec_probe.py`
  — the Stone's own mute may make most of it unnecessary; the Buds need it
  even less. Clipping the start of the owner's reply is the failure to
  watch for (the pre-roll ring fix in `stt_node` exists because of it).

**Exit test:** a 5-turn conversation ("Mitra, what time is it" / "and the
date" / "set a reminder" / "for six" / "thanks") with the name said once;
"stop" while driving halts the wheels within 300 ms when the robot is quiet;
a 20-minute idle room with the TV on → 0 turns.

---

## Phase 3 — Turn lifecycle hardening `[ ]`

**Why:** T5's "no double-speak, no dead air, no stuck mic". Cheap, independent
of hardware, and these are the bugs a demo hits. Each one gets a unit test in
`src/langrobo_core/tests/` (pure zone) or `src/pi5_voice_pkg/tests/`.

Candidates found reading the code on 2026-09-20 — **verify each on the robot
before fixing**, some may be theoretical:

- [ ] **Double-speak on a partial stream.** `agent_node._process`: if
  `speech_stream.chunks_sent > 0` but `spoke(response)` is false (the
  streaming run errored mid-way and the non-streaming fallback produced the
  final text; or the final text differs from the streamed text after
  normalisation), the whole response is re-published on top of the sentences
  already spoken. Decide: speak only the un-streamed remainder, or never
  re-speak once anything streamed.
- [ ] **Speech text hygiene.** Nothing between the LLM and TTS strips
  markdown (`**`, `*`, `#`, backticks), URLs, emojis or bracketed
  stage-directions; `SPEECH_STYLE` in the prompt asks the model not to emit
  them, which is a rule to measure. Add a deterministic `clean_for_speech()`
  in `speech_stream.py` applied per chunk, with tests. Telegram keeps the raw
  text.
- [ ] **Sentence splitter edge cases** (`split_sentences`): decimals and
  times ("at 10.30", "3.5 metres") currently split on the dot when followed
  by a space is absent — check; ellipsis runs; a chunk of exactly
  `_MIN_CHUNK_CHARS`; a `\n` immediately after an abbreviation. Table-driven
  test.
- [ ] **Barge-in latency.** `_turn_interrupt` is only polled between graph
  steps; a long generation or a blocking tool (timed drive, servo loop) delays
  it by the whole step. Measure worst case; if > 1 s, pass an abort into the
  tool layer (the motion tools already honour `request_motion_stop`).
- [ ] **Merge window** (`_on_user_input`, `_merge_window`): two utterances
  with different cloud STT latencies can merge out of order. Either stamp
  utterances with capture time in `/voice/stt_meta` and merge on that, or
  drop the merge now that the follow-up window exists on the STT side.
- [ ] **tts_node stop races.** `_on_stop` clears queues and closes the stream
  on the spin thread while `_play` writes on the playback thread — guarded by
  `_stream_lock` per chunk; confirm with a soak test (100 stops mid-sentence)
  that the process never hangs and `/voice/tts_speaking` always ends false.
- [ ] **Stuck-mic watchdog.** If `/voice/tts_speaking` has been true for
  longer than any plausible utterance (say 60 s) with no audio being written,
  `stt_node` unmutes itself and logs loudly. Belt-and-braces for a lost
  `<|eou|>`.
- [ ] **Cold-turn UX.** The 50–108 s first turn after a brain or Mac Mini
  restart looks like a dead robot. Speak a short "one moment, warming up"
  cue (Phase 1's `/voice/cue`) when a turn's first token has not arrived in
  N s, and fix the stale-connection half by giving the LLM HTTP client a
  short connect timeout + fresh connection on failure.

**Exit test:** the 182-test core suite grows to cover each item above; a
30-turn scripted session (mixed short/long answers, two barge-ins, one tool
error, one empty response) ends with the mic open and no sentence spoken twice.

---

## Phase 4 — Latency `[ ]`

**Why:** the remaining half of T5. Only after 0–3, because those change what
the timings even are.

- [ ] Build the MiniLM entry classifier from INTENT_ROUTING_PLAN.md
  (designed, not built): movement/stop from 2 LLM calls → 1, vision from 3 →
  1. Do **not** reintroduce regex intent matching (CLAUDE.md Layout).
- [ ] TTS cost per sentence: `sarvam_translate` is two API calls per
  sentence. Measure against having the brain answer in Telugu directly (one
  source of truth, one call) — PI5_VOICE.md already flags this as the
  long-term direction.
- [ ] STT: Sarvam is ~1 s per utterance. Evaluate streaming STT (Soniox
  WebSocket is already written but unverified — get a key) so transcription
  finishes ~when the user stops talking.
- [ ] Keep `scripts/latency_replay.py` as the scoreboard; record before/after
  waterfalls in this file.

**Exit test:** warm turn, simple question: end of user speech → first audio
≤ 1.5 s median over 20 turns. "Go forward" ≤ 1.0 s to wheels.

---

## Phase 5 — Music `[ ]`

**Why:** T6. Its own subsystem; **needs Phase 0** (the wake word must be heard
over music, which is louder and longer than any reply).

- [ ] Player node in `pi5_voice_pkg` (or a sibling package): local library
  first (`~/Music`, mpv/ffplay via PipeWire), streaming source second.
  Single new `/audio/*` contract, documented in PI5_VOICE.md — the old
  `/audio/music_*` topics had no listener; do not resurrect their names
  blindly.
- [ ] Ducking: music volume drops when `/voice/tts_speaking` or a wake cue
  fires, restores after. Wake word pauses; "stop" stops. Both must work
  *through* the music (Phase 2's speaking-branch listener, with music as
  another AEC reference).
- [ ] Tools: `play_music(query)`, `pause_music()`, `stop_music()` on `chat`
  (keep the tool set short — CLAUDE.md). Permissions: guest role may not
  control music (`services/permissions.py`).
- [ ] Degrade: no library / no network → the robot says it cannot play that,
  never crashes (CLAUDE.md #5).

**Exit test:** "Mitra, play some music" → audio within 3 s; "Mitra" over the
music at normal listening volume fires ≥ 9/10; "stop" halts it; asking a
question mid-song ducks, answers, restores.

---

## Phase 6 — Calling `[ ]`

**Why:** T7. Last because it is the largest new subsystem and the one with an
outside dependency. **Needs Phase 0** for the audio path.

**Route: VoIP — settled by the Bluetooth constraint.**
- **Bluetooth HFP to the owner's phone (car-kit model)** is ruled out: the
  Pi5 has one Bluetooth adapter and it cannot hold two HFP audio (SCO) links
  — the Stone already occupies it. (Would also need ofono or a custom HFP-HF
  implementation — heavy.)
- **VoIP (SIP provider, or Twilio Programmable Voice)** — audio stays in
  PipeWire, so it rides the same Stone sink/source + AEC that Phase 0 built;
  contacts live in a small `~/.langrobo/contacts.json`. Needs a paid number
  and internet.
- **Not available:** Telegram bot API has no voice calls; WhatsApp has no
  public calling API.

**Tasks (VoIP path):**
- [ ] SIP client on the Pi5 (pjsua2 / baresip) driven from a `call_node`;
  contract: `/call/dial`, `/call/hangup`, `/call/state`.
- [ ] While a call is up: TTS and music are suppressed, `stt_node` runs only
  the local "hang up"/wake spotter (it must not transcribe the far end).
- [ ] Tools: `call_contact(name)`, `hang_up()` — owner role only, confirmed
  by voice ("Call mom?" → "yes") before dialling. Missing SIP credentials →
  the tool explains it cannot call (CLAUDE.md #5).
- [ ] Incoming calls: announce the caller by name, "answer?" — later.

**Exit test:** "Mitra, call mom" → confirmation → ringing within 5 s → two-way
audio through the robot with no echo reported by the far end → "hang up" ends
it → the robot is listening again.

---

## Cross-cutting rules for every phase

- **The `/voice/*` wire protocol is shared with the Jetson's `speech_vision`
  repo.** Changes must be additive (new topics are fine — `/voice/*_meta`
  and `/voice/cue` follow that precedent); changing an existing message's
  meaning means editing both repos in the same sitting. `SPEECH_EOU` lives in
  `speech_stream.py` *and* `tts_node.py` — change both or neither (CLAUDE.md #4).
- **`langrobo_core` stays rclpy-free.** Anything testable (sentence splitting,
  speech cleaning, stop-word grammar) goes in the pure zone with tests.
- **Missing keys/devices degrade, never crash** (CLAUDE.md #5): every new
  provider, device or service needs the "what happens when it is absent" line
  in its docstring and a test.
- **Measure, then trust.** A phase is done when its exit test passed on the
  rover, not when the code reads right. Record the numbers here.
- **Keep prompts out of it.** None of this is fixed by another sentence in
  `prompts.py`; the 12B model has ignored such rules before (CLAUDE.md Gotchas).

## Decision log

| Date | Decision | By |
|---|---|---|
| 2026-09-20 | Roadmap written; phase order 0→6 agreed to be worked strictly in sequence | owner |
| 2026-09-20 | **Bluetooth speaker + mic (boAt Stone) stays** as the audio device; no wired speakerphone. AEC is done on the Bluetooth path (Stone firmware and/or PipeWire) | owner |
| 2026-09-20 | Calling route = **VoIP** (follows from the above: one BT adapter, one HFP link) | derived |
| 2026-09-20 | **No software AEC** — measured: Stone mutes its mic during playback, Buds cancel echo themselves; nothing inserted into the audio path | measured |
| 2026-09-20 | **Full-duplex barge-in dropped.** Owner: "on wake word, or after it responds, it listens" — the robot need not hear anyone while it talks | owner |
| — | HFP codec: mSBC available on the Stone? (Buds: yes, negotiated) | pending |
