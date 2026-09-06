# How LangRobo Actually Works — End-to-End Walkthrough

A narrative trace of the running system: what happens, in order, at every step.
ARCHITECTURE.md is the reference (layout, rules, tables); this file is the story.
All of this was verified live on 2026-07-03 (18 turns, zero errors).

---


## The machines and what runs on each

| Machine | Runs | Talks over |
|---|---|---|
| **Pi5** (this repo) | `langrobo-brain` (agent_node) + `langrobo-microros` (ESP32 bridge), both systemd; plus `pi5_voice_pkg` (CPU-only stt_node/tts_node — PI5_VOICE.md) | ROS2 DDS on Ethernet LAN |
| **Jetson Orin** | Perception: cuVSLAM + nvblox + Nav2, plus the phase-4 VLM bridge (`image_bridge` republishes the colour frame as JPEG for `look()`; `pixel_to_goal` turns a VLM-picked pixel into an odom-frame Nav2 goal). Voice is OFF here — it runs on the Pi5 (PI5_VOICE.md) | ROS2 DDS |
| **Mac Mini** | llama.cpp server, Gemma multimodal GGUF, 4 KV-cache slots | HTTP (OpenAI-compatible) |
| **ESP32** | wheel firmware — 2-motor diff drive, BTS7960 + encoders, 50 Hz PID | micro-ROS over WiFi UDP 8888 → Pi5 |

---

## 1. Boot — what happens when the Pi powers on

1. systemd starts `langrobo-microros.service` → `scripts/run_microros.sh` →
   micro-ROS agent listens on UDP 8888. The ESP32 (whenever it's powered)
   connects here and subscribes to `/cmd_vel`.
2. systemd starts `langrobo-brain.service` (3s grace for the network) →
   `scripts/run_brain.sh` → `ros2 launch langrobo_ros brain_launch.py
   start_micro_ros:=false` → **agent_node** comes up. Its init order
   (`langrobo_ros/agent_node.py`):
   1. Loads `~/ros2_ws/.env`, scrubs LangSmith tracing unless `LANGROBO_TRACING=true`.
   2. `services.config.load_settings()` — validates every LANGROBO_* value;
      a malformed one **crashes now** (systemd restarts; fix .env) rather than
      misbehaving at 2am.
   3. Installs JSON logging (every line gets the current turn's `trace_id`).
   4. Reads ROS params (`agent_params.yaml`): provider, model, locations. The
      **KV slot map is not a param** — it comes from `registry.SLOTS`, and
      agent_node probes llama.cpp's real slot count to check it fits.
   5. Creates **ROS2Bridge** (the only ROS I/O object) and injects it into
      `langrobo_core.tools._bridge` — from now on every tool can reach the robot.
   6. Configures the LLM factory (`services/llm.py`) + arms the cloud fallback
      if `LANGROBO_FALLBACK_*` keys exist.
   7. Starts the **Telegram** long-poll thread (no-op without a token +
      allowlist). Messages sent while the brain was down arrive now.
   8. Builds the LangGraph graph (once) — four nodes, from `registry.SPECS`.
   9. Subscribes: `/voice/user_input`, `/voice/tts_stop`, `/voice/{stt,tts}_meta`,
      `/camera/color/image_raw/compressed`.
   10. Starts the **health API** (FastAPI, port 8090) on a daemon thread.
   11. Starts the worker thread, says **"I'm ready"** through TTS, and
       background-prefills chat's llama.cpp slot so the first real turn is fast.

After boot the process has these threads:
```
ROS spin (main)     → caches the camera frame, queues inputs. Must never block.
worker (daemon)     → the ONLY thread that runs the graph/LLM
telegram poller     → long-polls getUpdates → worker queue
health API          → serves /health /status /metrics
nav worker          → transient; one Nav2 goal each
cache warmer        → transient; prefills LLM slots while idle
```

---

## 2. A voice turn, end to end ("what time is it?")

```
you speak → Jetson echo-cancelled mic (AEC: mic minus the robot's own audio)
  → openWakeWord neural keyword model (every 80ms chunk; audio not addressed
    to the robot is discarded BEFORE transcription)
  → wake heard → capture window → Silero VAD endpoints the utterance
  → Whisper STT → wake_gate strips the name
  → publishes String on /voice/user_input
```

(Wake word is "hey jarvis" until the custom hey_rakhi model is trained — see
JETSON_VOICE_UPGRADE.md. Saying the wake word while the robot is talking is
barge-in: the Jetson halts TTS, captures your utterance, and this brain
abandons its in-flight turn for the new one.)

1. **Spin thread** (`_on_user_input`): cancels any active navigation, interrupts
   any blocking motion (a new utterance always wins), stores the text as the
   pending user message, wakes the worker. If a previous message was still
   pending, the newer one replaces it.
2. **Worker thread** (`_process`): stamps a fresh `trace_id`, bumps
   `turns_total`, publishes `/brain/thinking=true`.
3. Builds the message list: persisted history + your new HumanMessage. Entry
   point is the **sticky agent** from last turn if it was chat/local_agent,
   else chat — so the common case costs ONE LLM call, no router hop.
4. **Graph runs** (`langrobo_core/graph/build.py`):
   - `turn_entry` resets the per-turn loop counters, routes to chat.
   - `chat` builds its prompt: static system prompt + HOUSEHOLD MEMORY block
     + today's date (never the clock — cache rule), binds its tools, and calls
     the LLM via `safe_invoke`.
   - The Mac Mini already has chat's prompt prefix cached in **slot 0**, so it
     only prefills your new sentence, then decodes.
   - Gemma decides it needs the clock → emits a `get_current_time` tool call
     → ToolNode runs it → result appended → loops back to chat → final answer.
5. **Streaming speech**: as answer tokens arrive, `SpeechStreamHandler` cuts
   them into sentences and publishes each on `/voice/robot_speech`
   immediately; the Jetson's Kokoro starts synthesizing the first sentence
   while the LLM is still writing the second. The utterance ends with the
   `<|eou|>` marker; tts_node holds `/voice/tts_speaking=true` (which mutes
   stt_node's capture) until it has played everything, plus a tail.
6. **After the turn**: the full message list (tool calls included) is kept as
   history; the **cache warmer** prefills the next turn's prompt in the
   background so it starts warm; `last_turn_duration_seconds` updates;
   `/brain/thinking=false`.

Observed live: `→ TTS: It's currently 3:04 PM on Friday, July 3, 2026.`

---

## 3. A vision turn ("what do you see?")

The Jetson camera continuously publishes JPEG frames; the bridge caches only
the latest one (age tracked).

1. chat recognizes a visual query → calls `handover("local_agent")` →
   `handle_handover` writes a routing note and chains to **local_agent**.
2. local_agent has no recent frame in its conversation → calls **`look()`**:
   grabs the cached JPEG (rejected if >10s old — "I cannot see right now"
   beats describing a stale scene), base64-encodes it, and injects it as a
   HumanMessage *image block* into the conversation.
3. The multimodal LLM call runs on **slot 1** (local_agent's dedicated
   KV slot) so the expensive image prefix stays cached and is never evicted
   by text agents. Gemma sees the pixels and answers.
4. The frame **stays in history** — a follow-up ("did he wear spectacles?")
   reasons over the same image with no re-capture. Other agents get an
   image-stripped projection of the same history (they see
   `[Current camera view]` as text).

Observed live: it described the actual room — "a large, textured grey blanket…
a laptop sitting on what looks like a bed."

---

## 4. A proactive turn (the robot speaks first)

"Go to the kitchen" returns immediately — Nav2 drives in the background and
the turn ends. A minute later the robot speaks without being spoken to:

1. `ROS2Bridge._nav_worker` (its own thread, one per goal) gets the action
   result from Nav2.
2. It calls the registered `_on_nav_done` callback, which injects
   `[SYSTEM] Navigation succeeded: I've arrived at 'kitchen'.` into the
   **system queue** (FIFO, never dropped, processed between user turns).
   If the goal came from Telegram, the injected text also carries a routing
   instruction so the report lands in that chat instead of the speaker.
3. System turns always enter at the **supervisor**, whose only ability is a
   grammar-forced `handover()` — it routes to chat.
4. chat's reply streams to TTS: **"I've arrived at the kitchen."**

**This is the pattern for every proactive behaviour.** A producer calls
`bridge.enqueue_system_turn(...)`; everything downstream already works. Don't
invent a second mechanism.

---

## 4b. A Telegram turn ("is anyone home? send me a pic")

You text the bot from outside the house:

1. The poller thread (`services/telegram.py`, `getUpdates` long-poll — no
   public IP needed) matches your chat_id against the allowlist, rate-limits,
   and drops the message into agent_node's **telegram FIFO**. Strangers are
   ignored silently; the getUpdates offset persists on disk, so texts sent
   while the robot was off are answered at boot.
2. The worker drains it **after** system events and any voice input (the
   person in the room wins) and runs a normal graph turn — same shared
   history, same KV-cache prefix — framed as
   `[Telegram from Rakesh] is anyone home? send me a pic`, with your name and
   role in state.
3. The agent answers with `look()` / `send_telegram_photo()` — the photo tool
   grabs a fresh camera frame and posts it to your chat. Capability checks run
   **inside the tools**: Mom (role `family`) texting "drive to the door" gets a
   polite refusal, and the attempt lands in the audit log.
4. The reply routes to your **chat, never the speaker** (typing indicator
   while it thinks; no TTS streaming, no barge-in).

Sending the bot a **photo** works too — it enters the turn as an image the
multimodal model can see (chat hands over to local_agent, same as camera
frames).

A drive you start from Telegram **reports back to your chat**, not to the
room: `tools/movement._last_nav_requester` remembers which channel asked, and
`_on_nav_done` writes that into the `[SYSTEM]` turn as a routing instruction.
Proactive pings respect `LANGROBO_QUIET_HOURS`; replies to your direct
messages always go through.

---

## 5. A movement turn ("move forward ten centimeters")

**This one never reaches an LLM.** `fastpath.match()` recognises it exactly
(`FastIntent(kind='move', args={'dir': 'F', 'cm': 10.0})`) and calls the same
tool the navigate agent would — so command-to-motion is milliseconds, not the
two LLM round-trips the graph would cost.

1. `fastpath.try_handle("move forward ten centimeters")` matches, speaks
   "Moving forward 10 centimeters." **first**, then calls `move_robot("F:10")`.
2. The tool computes the drive duration from calibrated wheel speed
   (`_PHYSICAL_VEL_MS`, see INTEGRATION_GAPS.md §3), then publishes Twist on
   `/cmd_vel` at 20Hz (feeding the ESP32's 500ms watchdog) until time is up,
   then publishes a zero Twist (stop).
3. micro-ROS agent forwards every Twist over UDP to the ESP32, whose 50 Hz PID
   tracks the commanded m/s against its encoders → wheels.
4. Interruptible at every tick: a new utterance or the spoken **"stop"**
   keyword (stt_node publishes `/voice/tts_stop`) sets the motion-interrupt
   event → the loop bails and stops the wheels immediately.

Anything the matcher is not *certain* about returns `None` and takes the full
graph instead: chat → `handover("navigate")` → the navigate agent. That is the
whole safety property — the fast lane never guesses.

`approach_described_object("the red bottle")` is the other shape: the VLM
looks at the current frame and points at a pixel, the Jetson's `pixel_to_goal`
deprojects that pixel with real depth into an odom-frame Nav2 goal with the
standoff already applied, and Nav2 drives there **avoiding obstacles**. It
returns as soon as the drive starts; arrival comes back later as a `[SYSTEM]`
turn. `navigate_to_pose("kitchen")` goes to a saved location the same way.

---

## 6. When things fail (by design, nothing crashes)

| Failure | What happens |
|---|---|
| Mac Mini unreachable | `safe_invoke` retries once → marks primary down 60s → uses cloud fallback if `LANGROBO_FALLBACK_*` armed, else **speaks** "My brain server is offline…". Primary re-probed after cooldown. |
| Camera feed dead | `look()` refuses frames >10s old → robot says it cannot see right now. |
| Tavily key absent | Web search is off; chat says it cannot look that up; zero boot spam. |
| Telegram token/allowlist absent | The channel stays off. The bot never talks to strangers. |
| Jetson depth grounding silent | `approach_described_object` says the depth service isn't answering — distinct from "grounding failed", which comes back with a reason. |
| Agent routing loops | Structural guards: max 3 handover visits + max 8 node runs per turn, self-handover nudged once — then a plain spoken fallback, no more LLM calls. |
| Graph exception | Caught in `_process` → "I'm having trouble right now" spoken, `turn_errors_total` bumped, next turn unaffected. |
| Process crashes | systemd restarts it in ≤10s; saved locations reload from disk. |
| Pi reboots | Both units auto-start; "I'm ready" announces recovery. |

---

## 7. Why it's fast (the KV-cache story)

The Mac Mini caches the LLM's processed prompt (KV cache) per slot. A warm
turn only pays for the *new* tokens; a cold one re-processes ~2k+ tokens
(~20s on the 12B). Everything below protects warmth:

- **One slot per agent**: supervisor=0, chat=1, local_agent=2, navigate=3.
  Four agents, four caches, nothing ever evicts anything. The map is declared
  beside the agents in `registry.py`; start llama.cpp with `--parallel 4`.
  This matters because it was measured: when the supervisor shared a slot with
  another agent (2026-07-06), each call evicted the other's prefix and cost
  18-50s of full-history re-prefill on the next turn.
- **Append-only history**: the per-agent projection never mutates or drops
  mid-history messages; trims happen only at turn boundaries, and the
  **cache warmer** re-prefills in the background right after each trim.
- **No clock in prompts** (the killer found in latency testing): only the
  date is in-prompt; the time is a tool.

Remaining latency is decode speed — a model choice (12B ≈ 9.5 tok/s; the 3n
E4B would roughly meet the ≤2s first-audio budget).

---

## 8. What's on disk

| Path | Contents |
|---|---|
| `~/.langrobo/locations.json` | spots saved with `save_location` |
| `~/.langrobo/telegram_offset` | inbound cursor — exactly-once across restarts |
| `~/.langrobo/telegram_deferred.json` | messages queued during quiet hours |
| `~/ros2_ws/.env` | keys + service settings (validated at boot) |
| `/etc/systemd/system/langrobo-*.service` | the two service units |
| journald | all logs (JSON, per-turn trace_id) |

Back up `~/.langrobo/` to preserve what the robot has been told; delete a file
to reset that piece.

---

## 9. Watching it work

```bash
journalctl -u langrobo-brain -f -o cat                     # live thoughts
journalctl -u langrobo-brain -o cat | jq 'select(.trace_id=="<id>")'  # one turn end-to-end
curl -s localhost:8090/status | jq                         # LLM/memory/turn state
python3 scripts/latency_replay.py "utterance"              # per-stage waterfall
```
