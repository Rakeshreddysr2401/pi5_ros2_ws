# How LangRobo Actually Works — End-to-End Walkthrough

A narrative trace of the running system: what happens, in order, at every step.
ARCHITECTURE.md is the reference (layout, rules, tables); this file is the story.
All of this was verified live on 2026-07-03 (18 turns, zero errors).

---


## The machines and what runs on each

| Machine | Runs | Talks over |
|---|---|---|
| **Pi5** (this repo) | `langrobo-brain` (agent_node) + `langrobo-microros` (ESP32 bridge), both systemd | ROS2 DDS on Ethernet LAN |
| **Jetson Orin** | Rover mode = perception: cuVSLAM + nvblox + Nav2 + YOLO detections_3d (`orin-nav-stack`). Voice role (`stt_node`/`tts_node`/`target_node`, separate `speech_vision` repo) is OFF on the Orin in rover mode | ROS2 DDS |
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
   4. Reads ROS params (`agent_params.yaml`): provider, model, KV slot map, locations.
   5. Creates **ROS2Bridge** (the only ROS I/O object) and injects it into
      `langrobo_core.tools._bridge` — from now on every tool can reach the robot.
   6. Configures the LLM factory (`services/llm.py`) + arms the cloud fallback
      if `LANGROBO_FALLBACK_*` keys exist.
   7. Starts **episodic memory** (`services/memory.py`): opens the embedded
      Qdrant store at `~/.langrobo/qdrant`, loads the fastembed model on a
      background writer thread — the brain never waits for it.
   8. Builds the LangGraph graph (once).
   9. Subscribes: `/voice/user_input`, `/voice/tts_stop`,
      `/camera/color/image_raw/compressed`, `/vision/target_result`.
   10. Starts the **health API** (FastAPI, port 8090) on a daemon thread.
   11. Starts the worker thread, says **"I'm ready"** through TTS, and
       background-prefills chat's llama.cpp slot so the first real turn is fast.

After boot the process has these threads:
```
ROS spin (main)     → fills caches (camera frame, YOLO result), queues inputs, runs timers
worker (daemon)     → the ONLY thread that runs the graph/LLM
memory writer       → embeds + upserts conversation turns (off the turn path)
health API          → serves /health /status /metrics
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
   `<|eou|>` marker; the Jetson holds `/voice/tts_speaking=true` (mic muted)
   until it has played everything.
6. **After the turn**: full message list (tool calls included) is persisted as
   history; the turn is queued to **episodic memory** (embedded on-device,
   written in the background); `last_turn_duration_seconds` gauge updates;
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

"Set a timer for one minute" → chat calls
`set_reminder(text="Your 1 minute timer is done", in_minutes=1)` → stored in
`~/.langrobo/reminders.json` (survives restarts).

Sixty seconds later:
1. A ROS timer (every 5s) polls the reminder store; the due reminder pops.
2. agent_node injects `[SYSTEM] Reminder due — announce to the user now: …`
   into the **system queue** (FIFO, never dropped, processed between user turns).
3. System turns always enter at the **supervisor**, whose only ability is a
   grammar-forced `handover()` — it routes to chat.
4. chat's reply streams to TTS: the robot announces **"Your 1 minute timer is
   done!"** with nobody having spoken to it.

Delivery polling (when a Swiggy order is active) and navigation-complete
events use the same producer pattern; future face-seen greetings will too.

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
   while it thinks; no TTS streaming, no barge-in). Memory records the turn
   with `person="Rakesh"`.

Sending the bot a **photo** works too — it enters the turn as an image the
multimodal model can see (chat hands over to local_agent, same as camera
frames). The middleman flow — "tell Mom I'll be late **and let me know what
she says**" — records an errand (`~/.langrobo/errands.json`, 24h expiry);
Mom's eventual reply comes back framed with it, and if you asked out loud, the
answer is announced through the section-4 proactive path. Proactive pings
respect `LANGROBO_QUIET_HOURS`; replies to direct messages always go through.

---

## 5. A movement turn ("move forward ten centimeters")

1. chat → `handover("navigate")` → navigate agent calls `move_robot("F:10")`.
2. The tool computes the drive duration from calibrated wheel speed, then
   publishes Twist on `/cmd_vel` at 20Hz (feeding the ESP32 watchdog) until
   time is up, then publishes a zero Twist (stop).
3. micro-ROS agent forwards every Twist over UDP to the ESP32 → wheels.
4. Interruptible at every tick: a new utterance or the spoken **"stop"**
   keyword (Jetson publishes `/voice/tts_stop`) sets the motion-interrupt
   event → the loop bails and stops the wheels immediately.

`navigate_to_visible_object("cup")` works the same way but closes the loop on
YOLO: Pi5 sets `/vision/target`, Jetson's target_node reports bearing/size on
`/vision/target_result`, and the servo loop turns/drives until the object
fills the frame. `navigate_to_pose("kitchen")` is the phase-2 Nav2 slot — it
honestly reports map navigation isn't available until SLAM lands.

---

## 6. When things fail (by design, nothing crashes)

| Failure | What happens |
|---|---|
| Mac Mini unreachable | `safe_invoke` retries once → marks primary down 60s → uses cloud fallback if `LANGROBO_FALLBACK_*` armed, else **speaks** "My brain server is offline…". Primary re-probed after cooldown. |
| Camera feed dead | `look()` refuses frames >10s old → robot says it cannot see right now. |
| Swiggy/Tavily key absent | Feature is off; agent says so; zero boot spam. |
| Memory backend broken | Memory reports unavailable in /status; everything else runs. |
| Agent routing loops | Structural guards: max 3 handover visits + max 8 node runs per turn, self-handover nudged once — then a plain spoken fallback, no more LLM calls. |
| Graph exception | Caught in `_process` → "I'm having trouble right now" spoken, `turn_errors_total` bumped, next turn unaffected. |
| Process crashes | systemd restarts it in ≤10s; reminders/memory/lists reload from disk. |
| Pi reboots | Both units auto-start; "I'm ready" announces recovery. |

---

## 7. Why it's fast (the KV-cache story)

The Mac Mini caches the LLM's processed prompt (KV cache) per slot. A warm
turn only pays for the *new* tokens; a cold one re-processes ~2k+ tokens
(~20s on the 12B). Everything below protects warmth:

- **Slot map**: chat=0, local_agent(images)=1, specialists(navigate/status/
  swiggy/tracker/knowledge/briefing/consolidation)=2, supervisor=3 —
  different prompts never evict each other. Supervisor got its own slot on
  2026-07-06: it fires on every `[SYSTEM]` turn, and sharing slot 2 meant it
  and whichever specialist was cached kept evicting each other (~18-50s
  full-history re-prefills).
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
| `~/.langrobo/household.json` | lists + facts (in-prompt memory) |
| `~/.langrobo/reminders.json` | pending reminders/timers |
| `~/.langrobo/qdrant/` | episodic memory vectors (embedded Qdrant) |
| `~/ros2_ws/.env` | keys + service settings (validated at boot) |
| `/etc/systemd/system/langrobo-*.service` | the two service units |
| journald | all logs (JSON, per-turn trace_id) |

Back up `~/.langrobo/` to preserve the robot's memory; delete a file to reset
that memory tier.

---

## 9. Watching it work

```bash
journalctl -u langrobo-brain -f -o cat                     # live thoughts
journalctl -u langrobo-brain -o cat | jq 'select(.trace_id=="<id>")'  # one turn end-to-end
curl -s localhost:8090/status | jq                         # LLM/memory/turn state
python3 scripts/latency_replay.py "utterance"              # per-stage waterfall
```
