# Mitra 2 — listen, speak, reason, act, remember (on the hardware we own)

**Status: plan. Written 2026-10-07** on branch `dev-1.6.0-agents`, after a web
survey of what is new in STT / TTS / models / agent memory (sources at the end)
and two measurements on our own Mac (§1). Nothing here is built yet. Each phase
has an exit test; per CLAUDE.md a phase is done when it is *measured*, not when
it is merged.

Hardware this plan is limited to — nothing new to buy:

| Box | What it has | Its job in Mitra 2 |
|---|---|---|
| Pi 5 (8 GB) | CPU, BT headset | **Ears + mouth + memory + conductor** |
| Mac Mini | llama.cpp b9830, Gemma 4 12B Q4, vision **and audio** in, 4 slots | **Brain + hearing** |
| Jetson Orin Nano Super (8 GB) | D555, RPLidar, cuVSLAM, nvblox, Nav2 | **Eyes + legs** |
| ESP32 | wheel PID | unchanged |
| Cloud (already paid for) | Sarvam (Telugu STT/TTS), Tavily | Telugu voice, web |

## 0. Status — 2026-10-07 evening

| | what | state |
|---|---|---|
| ✅ | **Entry classifier** on the Pi (`langrobo_core/routing`) — 70/70 right on real turns, skips 45 of 67 handovers (~3 s each) | built, `LANGROBO_INTENT_ROUTING=on` in `.env`; live after a brain restart |
| ✅ | **Facts memory** (`memory` tool, `~/.langrobo/memory.db`) — survives reboots | built; tested end to end on the Mac's Gemma |
| ⏳ | **Mac speed-up** (MTP drafter, `--swa-full`, `--metrics`) | written up: MAC_MINI_TASKS.md Task 3 + 5 — owner applies |
| ❌ | **Gemma as the ears** (§1A) | measured and rejected: Sarvam stays |
| ⏸ | Smart-Turn, speaker-ID, errands, object map, Pocket TTS, MCP / Home Assistant | next, in §5's order |

---

## 1. Two measurements that change the plan (2026-10-07)

**A. The Mac's Gemma can hear — but not well enough. REJECTED as the ears
(measured later the same day, below).** First impression: `/props` reports `audio: true`. Sent a WAV of
"Mitra, can you go near the red bag in the kitchen and tell me what is on it?"
straight to `/v1/chat/completions` (`input_audio`, slot 3):

| audio | asked for | got | time (cold) |
|---|---|---|---|
| clean 22 kHz | transcript | word-perfect | 3.6 s |
| **8 kHz + noise** (our BT mic) | transcript | perfect except the name ("Petra") | 3.4 s |
| 8 kHz + noise | JSON `{intent, text}` | `"move"`, correct text ("Mira") | 4.8 s |

Our current English fallback (Whisper `tiny.en` on the Pi) turns "what do you
see" into "think". That first sentence was Piper's clean synthetic voice. **The real test** — 14
utterances in Indian-accented English and Telugu (Sarvam TTS), degraded to the
BT mic's 8 kHz + noise, sent to Gemma and to today's Sarvam STT side by side:

| | Sarvam Saaras v3 (today) | Gemma 4 12B audio |
|---|---|---|
| latency | **0.2–0.4 s** | 4.5 s warm (2 s transcribe-only) |
| English | right (name → "Friend", handled by `wake_leading_aliases`) | "turn left ninety degrees" → "Metro, ton, neuf, neuf, neuf" |
| Telugu | right | invented sentences, or Hindi |
| routing in the same call | — | 6/14 |

So the "front door" (§3.2 below) is NOT built: Sarvam stays as the ears and
the routing hop is removed on the Pi instead (§3.2a). Re-test if the server
moves to a model with a stronger audio encoder; the bench is
`scratchpad`-style and quick to redo (synth with Sarvam TTS, 8 kHz + noise).

**B. Decode speed is 13.5 tok/s** (same calls). That speed is behind most of every wait.
llama.cpp gained Gemma 4 **MTP speculative decoding** in June 2026 (Gemma ships
a co-trained drafter; reported 2–3× on Apple Silicon). That alone could bring
a spoken reply from ~4 s to ~2 s.

---

## 2. The system in one picture

```
            ┌──────────────────── Pi 5 ─────────────────────┐
 mic ──► VAD ─► Smart-Turn v3 ─► wake check ─► audio clip ──┼──► Mac: FRONT DOOR (slot 3)
         (Silero) (end of turn,   ("mitra")                 │      Gemma hears the clip →
                  8 MB, ~12 ms)   + local STOP words ─► halt│      {text, agent, lang}
                                                            │            │
             speaker-ID (who is talking) ───────────────────┤            ▼
                                                            │   Mac: ONE agent call
  memory.db (facts · people · places · diary · photos) ◄────┤   chat | local_agent | navigate
                                                            │   (MTP drafter: ~2× decode)
 speaker ◄── TTS: Sarvam Bulbul v3 (Telugu, streamed) ◄─────┤            │
             fallback: Pocket TTS / Piper (local)           │            ▼
            └───────────────────────────────────────────────┘   tools → Jetson
                                                                 reach / goal_exec / look
                                                                 + NanoOWL object map
```

What changes against today (`HOW_IT_WORKS.md`):

| | today | Mitra 2 |
|---|---|---|
| hearing | Sarvam cloud → English text; `tiny.en` fallback | Gemma hears the audio on the Mac (English); Sarvam Saaras v3 stays for Telugu until Gemma's Telugu is measured |
| end of speech | silence timer | Smart-Turn v3 (listens to *how* you stopped) |
| routing | chat LLM + `handover` (2–3 LLM calls) | front door picks the agent in the same call that hears → **1 agent call** |
| vision backstop | hand-kept regex (`_VISION_QUESTION`) | the front door's label (regex kept only as a last net) |
| speed | 13.5 tok/s, cache rewind limit ~450 tok | MTP drafter + `--swa-full` |
| memory | photos (reset at boot) + 12 turns | facts, people, places, diary, photos — all survive reboot |
| objects | ask the VLM over photos (~4–5 s) | Jetson NanoOWL labels nvblox → instant "where is X"; VLM for questions |
| errands | one action + `then` | plan → do → check → retry, reported per step |

---

## 3. The parts

### 3.1 Ears (Pi 5)

- **VAD → Smart-Turn v3 → wake check.** Smart-Turn v3 (Pipecat, BSD, 8 MB,
  Whisper-tiny base, 23 languages, ~12 ms CPU) decides the person has *finished*,
  so no more cut-offs mid-sentence or 1.5 s of dead air. LiveKit's Turn
  Detector v1-mini (Qwen2.5-0.5B, Sept 2026, 14 languages) is the alternative;
  Smart-Turn is 60× smaller, which matters on the Pi. Verify Telugu on both.
- **STOP stays local and instant** (`_is_stop_command`): never waits for the Mac.
- **Who is talking:** a small speaker-embedding model (ECAPA / WeSpeaker,
  ~6 MB ONNX) enrolled with 3 phrases per family member. The turn then carries
  `speaker=Amma`, and `services/permissions.py` applies roles to voice the way
  it already does to Telegram.
- **Local transcript stays as backup:** Moonshine v2 (streaming, beats Whisper
  tiny by a wide margin, built for the Pi) replaces `tiny.en` for when the Mac
  is down.

### 3.2a Routing on the Pi — BUILT 2026-10-07

`langrobo_core/routing`: bge-small (fastembed, ~20 ms on the Pi) nearest-
example cosine over each agent's `examples` + `intent_examples` (never in a
prompt — no KV cache touched). Confident pick → enter that agent; unsure, or
nearest to an ABSTAIN reply ("yes", "do it") → today's sticky/chat rule.
Measured on the 188 distinct real utterances that entered chat in the
journal: **70 picks, 70 right; 45 of the 67 handovers skipped** (median 3.0 s
each). /status → `intent` keeps the live precision.

### 3.2 (REJECTED — see §1A) Hearing + routing = the front door (Mac, slot 3)

One request per utterance: the audio clip plus a fixed ~300-token prompt (the
three agents' one-line descriptions, rendered from `registry.py`, and "the
robot is called Mitra"). Output is grammar-constrained JSON:
`{"text": ..., "agent": "chat|local_agent|navigate", "lang": "en|te"}`.

- The prompt never changes, so it stays cached on slot 3; only ~100 audio tokens
  are new per turn. Target ≤ 1.5 s warm with MTP.
- `agent_node` enters that agent directly (`turn_entry` already supports a
  chosen entry — it is how sticky works). The `handover` tool and the
  agents' routing tables stay as the fallback for mid-turn topic changes.
- Telugu: if `lang == te` and Gemma's Telugu fails the eval, use the Sarvam
  transcript instead (it runs in parallel already; whichever is better wins).
- Typed input (Telegram, Studio) skips the audio; text goes through the same front
  door, or, cheaper, a MiniLM classifier on the Pi (INTENT_ROUTING_PLAN.md).
- Never guess: low-confidence or malformed → today's path (chat).

This is INTENT_ROUTING_PLAN.md with a better input: the model that routes has
heard the voice, not a possibly-wrong transcript.

### 3.3 Brain (Mac)

- **MTP speculative decoding** with Gemma 4's own drafter (`llama.cpp`
  PR #23398). Keep `--jinja --parallel 4`; add `--swa-full` (lifts the ~450
  token rewind limit that `photo_recall.py` works around) and `--metrics`.
  Needs ssh to the Mac + its RAM figure (the drafter and `--swa-full` both
  cost memory). `scripts/llm_cache_check.py` must still PASS.
- **Agents stay three** (one per modality, short tool lists). That design is
  right for a 12B model; what was wrong was the routing hop, fixed above.
- **Think only when it pays:** Gemma's thinking channel stays off for speech
  turns; the errand planner (3.6) turns it on for the one planning call.
- **Optional escalation** (owner decision, conflicts with the 2026-07 "no new
  cloud services" call): hard reasoning / long Telegram answers to a cloud
  model through the existing fallback ladder in `services/llm.py`.

### 3.4 Mouth (Pi 5)

- Primary stays **Sarvam Bulbul v3 streaming** (Telugu, ~0.8 s to first sound
  measured) — no open Telugu TTS beats it at that latency yet.
- English / offline: **Kyutai Pocket TTS** (100 M params, CPU, streaming,
  ~200 ms, voice cloning — Mitra can have *one* voice in both paths) replacing
  Piper, if it holds real-time on the Pi 5. Measure RTF before switching.
- The two-package protocol (`speech_stream.py` ↔ `tts_node.py`) is unchanged.

### 3.5 Memory — one file, `~/.langrobo/memory.db` (SQLite + vectors)

| store | holds | written by | read by |
|---|---|---|---|
| **core** | ≤150 tokens: who lives here, names, standing preferences | nightly consolidation | in every system prompt, at the END (like the date line) — so the cache re-reads once a day, not once a turn |
| **facts** | "Amma takes BP pills at 9", "Rakesh likes filter coffee" | `remember(fact)` tool + nightly extraction | `recall(query)` tool |
| **people** | voice prints, face crops, role, Telegram id | enrolment | speaker-ID, permissions |
| **places** | named spots, the saved map | `save_location` | navigate (needs Jetson map save + relocalise at boot — owner decision TODO #4) |
| **sightings** | object, where, when, which photo | photos + NanoOWL | `ask_photos`, "where did you last see X" |
| **diary** | a paragraph per day | nightly | `recall` |

- Retrieval is **tool-driven** (CLAUDE.md rule 3): `recall` returns the top
  few hits; nothing is auto-stuffed into prompts except the tiny `core` block.
- Extraction and consolidation run on the Mac's slot 3 **while idle**
  ("sleep-time compute"): read the day's turns, emit ADD / UPDATE / DELETE on
  facts (the mem0 pattern, ~92 on LoCoMo), and write the diary. No new server:
  we take the patterns from mem0 / Letta / Graphiti, not their runtimes (Letta
  runs its own agent loop and would fight LangGraph; mem0's graph mode is paid).
- Embeddings on the Pi: EmbeddingGemma-300M or bge-small (ONNX, a few ms).
- Facts carry `source` (who said it) and `valid_from` (the Graphiti idea:
  "the keys are in the drawer" is true *until* they are seen elsewhere).

### 3.6 Acting — errands that check themselves

A `run_errand(goal)` tool in navigate. One planning call (thinking on) produces
a short step list over existing tools (`approach_described_object`,
`navigate_to_pose`, `look`, `ask_photos`, `send_telegram_*`); **code** then runs
the steps, checks each result (`arrival_check` is the worked example), retries
once, and reports progress through the existing `[SYSTEM]` queue (rule 6).
"Go to the kitchen, see if the stove is on, message me" becomes three checked
steps instead of one hopeful turn. Teleop MANUAL still refuses every motion.

### 3.7 Eyes — a live object map (Jetson)

- **NanoOWL** (OWL-ViT on TensorRT, NVIDIA's ROS 2 node) or **YOLOE**, at a low
  rate and only while still, labelling nvblox depth → object positions in
  `odom`. That is the missing `/vision/detections_3d` contract
  (INTEGRATION_GAPS.md §1) restored with an open vocabulary.
- "Where is my bag?" → sightings table, instant. The VLM stays for questions
  about things ("what is on it?").
- Risk: Orin Nano GPU/CPU budget with nvblox + nav2 (rover OPEN_ISSUES #1/#2).
  Measure before keeping it on.

### 3.8 Reach — plug into the house

- **MCP server** exposing Mitra's safe tools (status, look, ask_photos, lists,
  reminders; motion only with the teleop gate) so Claude or a phone assistant
  can use the robot.
- **Home Assistant** as ONE chat tool `home(command)` (lights, fans, plugs) —
  one schema, not one per device.

---

## 4. Latency budget (warm turn, English)

| step | today | Mitra 2 target |
|---|---|---|
| end of speech detected | ~1.2 s silence | ~0.3 s (Smart-Turn) |
| transcript | Sarvam ~1 s | front door ~1.5 s (hears + routes) |
| routing hop | chat call + handover ~3–5 s (move / vision) | 0 |
| agent first sentence | ~2–4 s at 13.5 tok/s | ~1–2 s with MTP |
| first sound | ~0.8 s | ~0.8 s |
| **"go near the bag" → wheels move** | **~8–12 s** | **~4 s** |

Every number in the right column is a target until §5's eval says otherwise.

---

## 5. Phases (in order, each with its exit test)

| # | Phase | Needs from the owner | Exit test |
|---|---|---|---|
| 0 | **Eval set** (routing part done: the journal-mined 188-utterance set, §3.2a): ~50 real utterances (LangSmith traces + Telugu recordings) scored for agent, tool, latency; twin suite for motion | — | runs in one command; today's baseline recorded |
| 1 | **Mac speed**: MTP drafter, `--swa-full`, `--metrics` | ssh key on the Mac; its RAM | decode ≥ 25 tok/s; cache check PASS |
| 2 | **Front door**: Gemma hears + routes; Smart-Turn; name hint | — | eval: routing ≥ 95 %, transcript better than Sarvam on English; move/vision turns = 1 agent call |
| 3 | **Memory**: memory.db, remember / recall, core block, nightly consolidation, places persist | decide TODO #4 | "what do I like to drink?" answered after a reboot |
| 4 | **Who's talking**: speaker-ID + roles by voice | 3 phrases per person | 9 / 10 correct across the family |
| 5 | **Errands**: `run_errand` plan → do → check | floor test, owner watching | 3-step errand on the twin, then on the floor |
| 6 | **Object map**: NanoOWL on the Jetson | — | "where is my bag" < 1 s, Jetson CPU within budget |
| 7 | **Voice polish**: Pocket TTS fallback, Moonshine backup STT | — | offline mode speaks and hears |
| 8 | **Reach**: MCP server, Home Assistant | HA token | Claude can ask Mitra what it sees |

Phase 0 first because every later phase claims a speed-up or an accuracy gain,
and only the eval can show it.

## 6. Considered and not chosen

- **Whole LiveKit / Pipecat framework** — would replace the ROS voice trio
  and its safety (halt on every utterance). We take their turn models only.
- **Cloud speech-to-speech** (OpenAI Realtime, Gemini Live) — most natural,
  but it bypasses our tools, safety gates and memory, and costs per minute.
- **VLA models** (π0 etc.) — built for arms; an Orin Nano cannot run them
  beside nav.
- **A bigger local model** — the gain per second is worse than MTP + one fewer
  call on the 12B we have.

## Sources

- Gemma 4 audio in llama-server: <https://github.com/ggml-org/llama.cpp/discussions/21334>, <https://github.com/wilbowes/wyoming-gemma>
- Gemma 4 MTP in llama.cpp: <https://dev.to/everylocalai/how-to-get-2x-speed-on-gemma-4-with-multi-token-prediction-in-llamacpp-1b8e>, <https://hiesch.eu/blog/llamacpp-benchmarks-speculative-decoding/>
- Smart-Turn v3: <https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/>
- LiveKit Turn Detector v1: <https://livekit.com/blog/solving-end-of-turn-detection>
- Moonshine v2: <https://arxiv.org/html/2602.12241v1>
- STT survey 2026: <https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks>
- Saaras v3 / IndicConformer: <https://www.sarvam.ai/blogs/asr>, <https://arxiv.org/html/2608.08235>
- Pocket TTS: <https://kyutai.org/tts/>
- Indic TTS landscape: <https://caller.digital/blog/open-source-voice-ai-india-sarvam-ai4bharat-bhasini-2026>
- Agent memory 2026: <https://mem0.ai/blog/state-of-ai-agent-memory-2026>, <https://vectorize.io/articles/best-ai-agent-memory-systems>
- NanoOWL ROS 2: <https://github.com/NVIDIA-AI-IOT/ROS2-NanoOWL>; YOLOE: <https://learnopencv.com/yoloe-tutorial-real-time-open-vocabulary-detection/>
- ConceptGraphs: <https://www.emergentmind.com/papers/2309.16650>
