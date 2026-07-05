# LangRobo — Architecture

How the brain works, how the pieces fit together, and how to extend it.

---

## System overview

```
Mac Mini          llama.cpp at singireddys-mac-mini.local:8080 — Gemma multimodal GGUF (OpenAI-compatible HTTP)
Jetson Orin 8GB   USB cam · STT (Whisper) · TTS (Kokoro) · YOLOv8n (target_node) · (Isaac ROS SLAM/Nav2: phase 2)
Pi 5  [this repo] LangGraph supervisor + agents + services + micro-ROS agent (ESP32 bridge)
ESP32             4-wheel drive chassis (micro-ROS over WiFi UDP port 8888)
```

The Pi5 receives speech from the Jetson, runs the LangGraph decision graph, and
publishes responses back to the Jetson (TTS text, vision targets) and to the
ESP32 (movement via micro-ROS).

## The one hard rule

> **`langrobo_core` is a pure LangGraph zone — zero ROS2 imports.**
> Only `langrobo_ros` (agent_node.py, ros2_bridge.py) may import rclpy.

This used to be a convention inside one package; it is now a physical package
boundary. All graph logic, agents, tools, and services run and test on any
machine — the ROS2 side injects its bridge via `langrobo_core.tools._bridge.init()`
at startup, and `bridges.StubBridge` stands in when ROS2 is absent (Studio, tests).

## Package layout

```
src/langrobo_core/langrobo_core/       pip package (editable install via requirements.txt)
├── graph/
│   ├── build.py           StateGraph topology — the only file that knows how nodes connect
│   ├── state.py           AgentState TypedDict
│   ├── registry.py        Agent names/descriptions — single source of truth for routing
│   ├── turn_entry.py      Start of every turn: resets loop guards, sticky routing
│   └── handover_resolver.py  Centralized handover: chain vs sticky, loop guard
├── prompts.py             EVERY system prompt in the brain (agents + background jobs) —
│                          agents import from here; only dynamic blocks are appended in-module
├── agents/                One module per agent (node fn + dynamic context assembly)
│   ├── supervisor.py      Pure router — grammar-forced handover(), never speaks
│   ├── chat.py            Default responder — general Q&A, web search, reminders, memory, music
│   ├── local_agent.py     Multimodal vision — reasons over real frames via look()
│   ├── navigate.py        Movement: fine Twist + YOLO visual servoing + Nav2 slot
│   ├── status.py          Robot operational state
│   ├── swiggy.py          Food ordering (MCP; degrades cleanly without a token)
│   ├── tracker.py         Delivery tracking + door navigation
│   ├── knowledge.py       Q&A over ingested household documents (manuals, notes)
│   └── briefing.py        Morning briefing (scheduled [SYSTEM] + on-demand)
├── tools/                 @tool functions; __init__.py holds per-agent tool sets
├── services/
│   ├── config.py          Validated .env settings — fail fast on malformed values
│   ├── llm.py             LLM factory + slot pinning + cloud-fallback policy
│   ├── memory.py          Episodic memory (embedded Qdrant + on-device fastembed)
│   ├── consolidation.py   Nightly episodic→facts distillation (local model only)
│   ├── knowledge.py       Document ingest: extract → chunk → embed → Qdrant
│   ├── briefing.py        Once-daily briefing scheduler ([SYSTEM] producer state)
│   ├── watch.py           Home watch mode — armed person-detection alerts
│   ├── health.py          In-process FastAPI: /health /status /metrics (bearer token)
│   ├── logging.py         JSON logs + per-turn trace IDs (ContextVar)
│   └── metrics.py         Tiny thread-safe counter/gauge registry (Prometheus text)
├── utils/                 history.py · message_utils.py · speech_stream.py · timing.py
└── bridges/stub.py        StubBridge — full brain without ROS2

src/langrobo_ros/          ament_python package
├── langrobo_ros/agent_node.py   ROS2 entry point — params, subscriptions, worker loop
├── langrobo_ros/ros2_bridge.py  ALL ROS2 I/O (topics / services / actions)
├── launch/brain_launch.py       micro-ROS agent (optional) + agent_node
├── config/agent_params.yaml     LLM + robot ROS parameters
└── systemd/                     langrobo-brain.service · langrobo-microros.service

src/robot_interfaces/      Custom interfaces (FindObjectPose.srv — phase-2 depth)
```

## Two entry points

The same graph is driven two ways — never simultaneously.

| | `ros2 launch` / systemd | `langgraph dev` |
|---|---|---|
| Entry file | `langrobo_ros/agent_node.py` | `graph_studio.py` |
| Input source | `/voice/user_input` (Jetson STT) | Studio browser UI |
| Bridge | `ROS2Bridge` | `ROS2Bridge` if ROS2 sourced, else `StubBridge` |
| State | in-node history list (bounded) | Studio in-memory checkpointer |
| LLM config | `agent_params.yaml` ROS params | `.env` `STUDIO_*` vars |

`StubBridge` serves `STUDIO_TEST_IMAGE` (a JPEG path) as the camera frame so
`look()` vision is testable off-robot.

## Thread model

```
ROS2 spin thread (main)           worker thread (daemon)          service threads
        │                                  │                       memory writer (embeds+upserts)
  fills sensor caches               drains input queue             health API (uvicorn)
  queues user/system turns          calls graph.stream()           cache warmer (transient)
  fires poll timers                 runs LLM + tools
        │                                  │
        └──── input queue ────────────────┘
```

The spin thread never blocks on LLM work; the worker never touches ROS2
directly; memory embedding never runs on the turn path.

## Graph topology

```
START → turn_entry ──► sticky agent (chat/local_agent) │ supervisor ([SYSTEM]) │ chat (default)
                                  │
supervisor ──► supervisor_tools ──► handle_handover ──► [chat|local_agent|navigate|status|swiggy|tracker]
                                                              │
                                                    per-agent tool nodes
                                                              │
                                                       handle_handover
                                                    chain=True → next agent (same turn)
                                                    chain=False → END (sticky next turn)
```

- Fresh user turns enter at **chat**, which answers directly or hands over —
  ONE LLM call in the common case (no router hop).
- The **supervisor** runs only for `[SYSTEM]` events and mid-turn handbacks; its
  handover is grammar-forced (`tool_choice`) so it can never emit stray text.
- **Loop safety is structural**, independent of the model: per-turn visit
  counter in `handover_resolver` (max 3 per agent), per-turn node-run counter in
  `build.py` (max 8), self-handover → one "answer now" nudge. On overflow the
  turn ends with a plain spoken fallback, no further LLM calls.

## Response contract

- The agent's **message text IS the speech**: streamed to `/voice/robot_speech`
  sentence-by-sentence as the LLM generates (`utils/speech_stream.py`);
  agent_node closes each utterance with the `<|eou|>` marker. There is no
  `speak()` tool. Text alongside a tool call ("Let me check.") is spoken while
  the tool runs.
- `safe_invoke` (utils/message_utils.py) wraps every agent LLM call:
  retry → primary-down cooldown → cloud fallback → spoken degraded message.
  The brain never dies because a server did.

## LLM: local-first with cloud fallback

Primary is the Mac Mini llama.cpp server (config: `agent_params.yaml`).
Fallback is armed via `.env` (`LANGROBO_FALLBACK_PROVIDER/MODEL/API_KEY_ENV`) —
until keys exist, the robot degrades to a spoken offline message instead.

Policy (`services/llm.py`): a connection-class failure marks the primary down
for 60s; calls go straight to the fallback during the window; the primary is
re-probed after. Cloud fallbacks are multimodal (openai/anthropic/gemini), so
`look()` vision survives failover. Streaming TTS works on openai-compatible
fallbacks; others publish the whole reply (protocol unchanged).

### KV-cache discipline (llama.cpp)

Everything below exists to keep warm turns pure-decode (~20s prefill avoided):

- **Slot map** (`id_slot` per request): chat=0, local_agent(images)=1,
  supervisor+specialists=2. Keeps each prompt prefix hot across excursions.
- **Append-only projection** (`utils/message_utils.py`): for any log L and
  suffix S, project(L) must be a prefix of project(L+S). Historical routing
  notes are never dropped; images are stripped per-message for text agents.
- **Trim at turn boundaries only** (`utils/history.py`): the ONE deliberate
  cache reset. Old camera frames are evicted at that free moment (newest 2 kept).
- **Background warmer** (agent_node): after boot and after every trim,
  re-prefills the next agent's exact prompt with max_tokens=1 while idle.
- **No clocks in prompts**: only the date (changes daily) is in-prompt; the
  clock is `get_current_time()` — a per-minute timestamp would re-prefill ~2k
  tokens every turn.

## Conversational vision — local_agent

`local_agent` (multimodal Gemma) reasons over the **actual camera frame**:
`look()` grabs the cached Jetson JPEG and injects it as a HumanMessage image
block (OpenAI-compatible servers only honour images in user-role messages).
The frame **stays in history**, so follow-ups ("did he wear spectacles?")
reason over the same pixels without re-capture. Frames older than 10s are
refused — better to admit blindness than describe a long-gone scene.

One shared conversation log, projected per-agent: `local_agent` gets the
image-preserving projection; every other agent gets image-stripped text.

## Memory: three deliberate tiers

| Tier | Store | Recall | Why |
|---|---|---|---|
| Household facts + lists | JSON (`~/.langrobo/household.json`) | **in-prompt** — always visible | dozens of facts; guaranteed recall beats retrieval |
| Episodic (conversations) | **Qdrant** embedded (`~/.langrobo/qdrant`) | `recall_memory(query)` tool | unbounded history can't fit a prompt |
| Consolidated facts | `facts` collection (same store) | merged into `recall_memory` results | nightly distillation of episodes — the "self-learning" tier |
| Document knowledge | `knowledge` collection (same store) | `search_documents` (knowledge agent) | manuals/notes sent to the robot — chunked + embedded; re-sending a file replaces it |
| Visual household memory | reserved `visual` collection | phase P4 | "where did I leave my keys" |

Episodic details (`services/memory.py`): every user turn is embedded
**on-device** (fastembed ONNX, bge-small-en-v1.5, 384-dim) on a writer thread
and upserted with payload `{user, robot, agent, person(nullable), ts}` — the
`person` field is ready for P2 face recognition with no migration. Recall is
tool-driven, never auto-injected into system prompts (that would churn the KV
cache every turn). Set `QDRANT_URL`/`QDRANT_API_KEY` to swap to a server or
Qdrant Cloud — config only.

## Navigation (phase-2 slot)

Three modes, two live today:

1. **Fine movement** — `move_robot("F:20"/"L:90"/"S")` → timed Twist on
   `/cmd_vel` → micro-ROS → ESP32. Interruptible: new user input or the spoken
   "stop" keyword aborts mid-move.
2. **Object approach** — `navigate_to_visible_object("cup")`: Jetson YOLOv8n
   publishes bearing/size on `/vision/target_result`; the servo loop turns and
   drives until close. Mono cam — no obstacle avoidance.
3. **Map navigation (phase 2)** — `navigate_to_pose("kitchen")` sends a Nav2
   action goal. Until Nav2/SLAM exists on the Jetson it reports honestly that
   map navigation is unavailable (10s server wait → spoken failure). The
   interfaces are the slot: `/goal_pose` plumbing, locations in
   `agent_params.yaml`, odometry topic reserved.

## ROS2 topic contract (Jetson-facing, unchanged in the restructure)

| Topic | Type | Direction | Notes |
|-------|------|-----------|-------|
| `/voice/user_input` | String | Jetson → Pi5 | STT output — triggers a turn |
| `/voice/robot_speech` | String | Pi5 → Jetson | streamed sentence chunks; utterance ends with `<\|eou\|>` |
| `/voice/tts_speaking` | Bool | Jetson → Pi5 | mic muted (half-duplex) across chunk gaps |
| `/voice/tts_stop` | String | Jetson → Pi5 | "stop" keyword — also halts wheels |
| `/camera/color/image_raw/compressed` | CompressedImage | Jetson → Pi5 | JPEG cached for look() |
| `/vision/target` | String | Pi5 → Jetson | COCO class to hunt ("" = stop) |
| `/vision/target_result` | String (JSON) | Jetson → Pi5 | `{target, found, bearing_x, rel_size, conf, stamp}` |
| `/audio/music_cmd` | String (JSON) | Pi5 → Jetson | `{action: play\|pause\|resume\|stop\|volume, ...}` → music_node (see JETSON_VOICE_UPGRADE.md) |
| `/audio/music_state` | String (JSON) | Jetson → Pi5 | `{playing, paused, title, volume, error, stamp}` — cached by bridge |
| `/goal_pose` | PoseStamped | Pi5 → Jetson | Nav2 goal (phase 2) |
| `/visual_slam/tracking/odometry` | Odometry | Jetson → Pi5 | robot pose (phase 2) |
| `/camera/pan_tilt_cmd` | String (JSON) | Pi5 → ESP32 | reserved — pan-tilt servos (phase 2, see tools/movement.py notes) |
| `/cmd_vel` | Twist | Pi5 → ESP32 | wheels via micro-ROS |
| `/diag/timing` | String (JSON) | both | per-stage latency events (scripts/latency_replay.py) |
| `/brain/thinking` | Bool | Pi5 | True while LLM running |

## Self-initiated turns (proactive speech)

Producers inject `[SYSTEM]` turns into agent_node's system queue (FIFO, never
dropped, processed between user turns, always entering at the supervisor):

| Producer | Trigger | Injects |
|----------|---------|---------|
| Reminder poll | 5s timer | "[SYSTEM] Reminder due — announce to the user now: …" |
| Delivery poll | 120s timer (order active) | "[SYSTEM] Check if order {id} has been delivered" |
| Nav completion | event | "[SYSTEM] Navigation succeeded/failed: …" |
| Watch poll | 2s timer (armed + person seen) | "[SYSTEM] Watch alert — … announce aloud …" |
| announce_at_home tool | Telegram sender asks for a spoken message | "[SYSTEM] {sender} asks (via Telegram) to announce this aloud …" |
| Briefing scheduler | 60s timer (≤ once/day at LANGROBO_BRIEFING_HOUR) | "[SYSTEM] Morning briefing time — deliver the household briefing now." |

New proactive features (P2 face-seen greeting, presence events) follow the same
producer pattern.

## Home watch mode (vision-triggered proactive alerts)

`services/watch.py` + `tools/watch.py` + agent_node's watch poll. Armed
explicitly ("watch the house" — voice or Telegram, CAP_WATCH gated:
owner/family only); while armed the poll keeps the Jetson target finder
hunting the COCO class `person` over the EXISTING `/vision/target` contract —
no new Jetson code. A confident detection outside the cooldown does two
things, in order:

1. **Deterministic photo alert** to every owner-role Telegram member
   (spin-thread-safe: the send runs on a short-lived background thread).
   This path has no LLM in it — a security alert must not depend on the Mac
   Mini being up. It also bypasses quiet hours on purpose.
2. **`[SYSTEM]` watch-alert turn** (the standard producer pattern) so the
   robot announces aloud what it saw and did.

Armed state persists in `~/.langrobo/watch.json` (a restart must not silently
disarm the house). The poll never runs during a turn, so it can't fight the
visual-servo loop over `/vision/target`. When face recognition lands (P2),
the upgrade path is alert-on-strangers-only — same plumbing.

## Memory consolidation (self-learning, phase 1)

`services/consolidation.py`, prompt in `prompts.py` (CONSOLIDATION_PROMPT).
Once per day at/after `LANGROBO_CONSOLIDATION_HOUR`, while idle and only on
the LOCAL model (never the cloud — household chatter stays home): episodes
newer than a persisted cursor are batched through the LLM, which returns a
strict-JSON array of durable third-person facts; each is embedded, deduped
(cosine ≥ 0.92 against existing facts) and stored in the `facts` collection.
`recall_memory` merges facts with episodic hits, so "Rakesh likes his coffee
black" survives months after the raw turn scrolled away. LLM calls ride the
specialist slot — a 3am run never evicts chat's hot KV prefix. The run aborts
between batches the moment real input arrives and resumes later the same day.
A future fine-tune pipeline would consume the same `facts` collection as its
curated dataset — that is the reserved slot for PRD §6's "train monthly".

## Telegram channel (second front door)

`services/telegram.py` (pure zone) long-polls `getUpdates` on a daemon thread —
no public IP needed. Allowlisted members' messages (text and photos) enter
agent_node's telegram FIFO (drained after system events and voice: the person
in the room wins), become turns framed `[Telegram from <name>]` in the SAME
shared history and KV-cache prefix as voice, and route their reply back to the
sender's chat — never the speaker. Telegram turns don't stream and can't be
barge-in aborted; voice turns byte-for-byte unaffected.

The one sanctioned Telegram→speaker path is `announce_at_home`
(tools/announce.py, CAP_ANNOUNCE): it enqueues a `[SYSTEM]` turn — the
standard producer pattern — so the robot says the message aloud via the
normal proactive-speech path and it lands in shared history. Policy: a bare
"tell Mom X" (either channel) asks the sender back (phone or aloud?); quiet
hours refuse with alternatives unless the sender explicitly insists
(`override_quiet_hours`). The ask-back is ENFORCED in code, not prompts
(`tools/_relay_confirm.py`): the send tools refuse a bare relay until the
user's own words name the channel or a new user turn answers the pending
ask — confirmation cannot be minted inside the requesting turn. Bypasses:
`[SYSTEM]` scheduled relays, errand forwards, `report_back=True` (a
collected answer needs the phone by construction). Tools reach the system
queue via `bridge.enqueue_system_turn()` (agent_node registers
`_enqueue_system` at startup, mirroring the nav-done callback).

Trust model (`services/permissions.py`): Telegram gives verified identity
(chat_id → name + role); role→capability checks are enforced **inside the
tools** (`tools/telegram.py`, movement, photos), never only in prompts. Voice
has no identity until P2 speaker/face recognition and acts as owner.
Episodic memory writes for telegram turns fill the reserved `person` field.

The middleman flow persists in `tools/errands.py` (`~/.langrobo/errands.json`):
`send_telegram_message(report_back=True)` records an errand; the recipient's
next message is framed with it (telegram-asked → agent forwards the answer;
voice-asked → a `[SYSTEM]` turn announces it aloud — the standard proactive
path). Proactive pings respect `LANGROBO_QUIET_HOURS` via a persisted deferred
queue flushed by the poller.

## Observability

- **Structured logs**: every line is one JSON object with a per-turn `trace_id`
  (`services/logging.py`); `journalctl -u langrobo-brain -o cat | jq`.
- **Health API** (`services/health.py`): `/health` (liveness), `/status` (LLM
  health, memory, last turn, queues, frame age), `/metrics` (Prometheus text).
  Bearer-token secured; tokenless mode is forced to localhost.
- **Timing events**: `/diag/timing` per-stage waterfall via
  `scripts/latency_replay.py`.

## Patterns considered and rejected (and when to revisit)

Asked for explicitly (ThingsToDo #3/#5, 2026-07-06); the answer is recorded
so it isn't re-litigated every few months. Every rejection is about the same
constraint: **one 12B model on the Mac Mini and a ≤2s first-audio budget** —
every extra LLM hop or cache-thrashing prompt costs real seconds.

- **langgraph-swarm / langgraph-supervisor libraries**: the hand-rolled
  supervisor + handover registry IS this pattern, minus abstraction layers we
  can't tune (slot pinning, grammar-forced handover, loop guards). Revisit if
  the agent count triples or we hire contributors who know the libraries.
- **Subgraphs**: useful when an agent needs private multi-node state. Every
  agent here is one node + one tool node sharing one message log (that
  sharing IS the KV-cache strategy). Adopt per-agent the day one genuinely
  needs an internal pipeline — build.py can mount a compiled subgraph as a
  node without touching the others.
- **DeepAgents / background task lane**: rejected for the realtime loop
  (PRODUCT.md); long-horizon work rides the errand ledger + [SYSTEM]
  producers instead. Revisit for genuinely long tasks (multi-step web
  research) — as a separate low-priority queue, never inside a voice turn.
- **`interrupt()` (LangGraph human-in-the-loop)**: needs a checkpointer;
  production deliberately keeps history in-process (agent_node owns it for
  trimming + cache warming). Our confirms ("phone or aloud?") are plain
  conversational turns — the reply ends the turn, the user's answer is the
  next turn. Same UX, zero infra.
- **Custom stream modes (`custom`, `updates`)**: the pipeline already streams
  at the right grain — sentence chunks to TTS via callbacks
  (`speech_stream.py`) while `stream_mode="values"` drives turn logic.
  Finer-grained streaming has nothing to feed: TTS is the only consumer.
- **Folder-per-concern renames** (`nodes/`, `swiggy_food_agent`, …): the
  separation exists — `graph/` is topology+plumbing nodes, `agents/` is one
  module per agent, `tools/`, `services/`, prompts in `prompts.py`, and the
  ROS boundary is a package split. Renaming working agents ("chat" →
  "general_query_agent") would churn the registry, handover Literal, slot
  map, sticky logic and tests for zero behaviour. New agents get the
  descriptive names (`swiggy` predates the convention; rename it the next
  time its contract changes anyway).

## How to add an agent

1. Write its system prompt in `prompts.py` (start from CHAT_PROMPT's shape;
   prepend PERSONA for any user-facing agent)
2. Create `agents/<name>.py` (copy `chat.py` as template) importing that prompt
3. Add tools in `tools/`, a named tool set in `tools/__init__.py`
4. Register in `graph/registry.py` (name + description + examples)
5. Add one line to `_AGENT_SPECS` in `graph/build.py`
6. Add the name to the `handover` tool's `next_agent` Literal (`tools/handover.py`)

## How to add a tool

1. Write it in `tools/` with `@tool`, using `_bridge.get()` for robot I/O
2. Add it to the agent's tool set in `tools/__init__.py`
3. Mention it in that agent's system prompt (`prompts.py`)
