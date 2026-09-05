# LangRobo — Architecture

How the brain works, how the pieces fit together, and how to extend it.

---

## System overview

```
Mac Mini          llama.cpp at singireddys-mac-mini.local:8080 — Gemma multimodal GGUF (OpenAI-compatible HTTP)
Jetson Orin 8GB   Rover mode = PERCEPTION: cuVSLAM + nvblox 3D map + Nav2 + YOLOv8n detections (2D bearing + 3D metric), `orin-nav-stack`. Voice (STT/TTS, separate `speech_vision`) is a different role, OFF on the Orin in rover mode
Pi 5  [this repo] LangGraph supervisor + agents + services + micro-ROS agent (ESP32 bridge); also runs pi5_voice_pkg (CPU-only STT/TTS, PI5_VOICE.md) so voice works while the Jetson is in rover mode
ESP32             2-motor differential drive: BTS7960 + encoder motors, 50 Hz PID (micro-ROS over WiFi UDP port 8888)
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
│   ├── turn_entry.py      Start of every turn: resets loop guards, sticky routing
│   └── handover_resolver.py  Centralized handover: chain vs sticky, loop guard
├── prompts.py             EVERY system prompt in the brain (agents + background jobs) —
│                          agents import from here; only dynamic blocks are appended in-module
├── registry.py            One AgentSpec per agent — THE source of truth: routing copy,
│                          prompt, tool set, stickiness, MCP binding, dynamic context
├── agent_ids.py           Agent names only (leaf module — the handover grammar reads it)
├── agents/                factory.py builds every agent node from its spec;
│                          supervisor.py is the one hand-written node (forced tool_choice)
│   ├── supervisor.py      Pure router — grammar-forced handover(), never speaks
│   ├── chat.py            Default responder — general Q&A, web search, reminders, memory, music
│   ├── local_agent.py     Multimodal vision — reasons over real frames via look()
│   ├── navigate.py        Movement: fine Twist + YOLO visual servoing + Nav2 slot
│   ├── status.py          Robot operational state
│   ├── swiggy.py          Food ordering (Swiggy MCP; degrades cleanly without a token)
│   ├── instamart.py       Grocery ordering (Swiggy Instamart MCP; same degrade)
│   ├── dineout.py         Table reservations (Swiggy Dineout MCP; same degrade)
│   ├── tracker.py         Food + grocery delivery tracking + door navigation
│   ├── knowledge.py       Q&A over ingested household documents (manuals, notes)
│   └── briefing.py        Morning briefing (scheduled [SYSTEM] + on-demand)
├── tools/                 @tool functions; __init__.py holds per-agent tool sets
├── services/
│   ├── config.py          Validated .env settings — fail fast on malformed values
│   ├── llm.py             LLM factory + slot pinning + cloud-fallback policy
│   ├── mcp.py             MCP provider registry — remote tool servers + token lifecycle
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
├── langrobo_ros/studio_voice_node.py  dev mode only: voice ↔ langgraph dev
├── launch/brain_launch.py       micro-ROS agent (optional) + agent_node
├── launch/studio_voice_launch.py      dev mode: STT + TTS + the voice bridge
├── config/agent_params.yaml     LLM + robot ROS parameters
└── systemd/                     langrobo-brain.service · langrobo-microros.service

src/robot_interfaces/      Custom interfaces (FindObjectPose.srv — phase-2 depth)
```

## Two entry points

The same graph is driven two ways — never simultaneously.

| | `ros2 launch` / systemd | `langgraph dev` |
|---|---|---|
| Entry file | `langrobo_ros/agent_node.py` | `graph_studio.py` |
| Input source | `/voice/user_input` (STT) | Studio browser UI, **or** `/voice/user_input` via `studio_voice_node` |
| Bridge | `ROS2Bridge` | `ROS2Bridge` if ROS2 sourced, else `StubBridge` |
| State | in-node history list (bounded) | server-side thread (checkpointer) |
| LLM config | `agent_params.yaml` ROS params | `.env` `STUDIO_*` vars |

`StubBridge` serves `STUDIO_TEST_IMAGE` (a JPEG path) as the camera frame so
`look()` vision is testable off-robot.

### Voice in dev mode

`langgraph dev` serves the graph over HTTP and has no ROS side, so dev mode
would lose both the mic and the speaker — agent_node owns the input queue and
the reply sink, and the graph itself never speaks (no `speak()` tool).
`studio_voice_node` (+ `services/studio.py`, pure zone) is that pair and
nothing else: `/voice/user_input` in, `/voice/robot_speech` + `<|eou|>` out,
carrying the turn over HTTP to `:2024`.

It works in both directions — a spoken turn runs on the bridge's own thread
(so it appears in the Studio UI), and a turn typed into the Studio box is
picked up by a watcher and spoken. `join_stream` does not replay a run's token
stream to a late joiner, so watched turns are spoken from their final `values`
snapshot rather than token by token; driven turns still stream sentence by
sentence. What dev mode does NOT get: `[SYSTEM]` turns, Telegram, the fast
path, history trimming — all agent_node features, deliberately not duplicated.
Runbook + arguments: OPERATIONS.md § Voice in dev mode (Studio).

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
supervisor ──► supervisor_tools ──► handle_handover ──► [chat|local_agent|navigate|status|swiggy|instamart|dineout|tracker|knowledge|briefing]
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
  specialists (status/swiggy/instamart/dineout/tracker/knowledge/briefing)=2, supervisor=3,
  navigate=4. Keeps each prompt prefix hot across excursions. The supervisor
  got its own slot on 2026-07-06: it fires on every [SYSTEM] turn, and sharing
  slot 2 meant supervisor and the cached specialist evicted each other
  (~18-50s full-history re-prefills). Navigate got its own slot for the same
  reason — it's the most latency-sensitive specialist (real movement) and
  previously shared slot 2 with four rarely-active agents. Both are also
  cache-warmed independently by `agent_node`'s background warmer (supervisor
  since 2026-07-06; navigate is not yet warmed proactively — only chat/
  local_agent/supervisor are).
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
| Where objects are | JSON (`~/.langrobo/world_model.json`) | `where_is` / `approach_object` | positions must outlive the process, like saved locations always did |
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
- **`TimeoutPolicy` per-node timeouts (langgraph 1.2)**: evaluated during the
  2026-07-06 upgrade to langgraph 1.2.7 and NOT adopted — it relies on
  asyncio cancellation, and every node here is synchronous (blocking LLM
  invoke, `time.sleep` servo loops), so it would never fire. Turn safety
  stays with the existing layers: httpx timeouts inside safe_invoke, tool-
  internal deadlines, and the loop guards. Revisit only if nodes go async.
- **Custom stream modes (`custom`, `updates`)**: the pipeline already streams
  at the right grain — sentence chunks to TTS via callbacks
  (`speech_stream.py`) while `stream_mode="values"` drives turn logic.
  Finer-grained streaming has nothing to feed: TTS is the only consumer.
- **Folder-per-concern renames** (`nodes/`, `swiggy_food_agent`, …): the
  separation exists — `graph/` is topology+plumbing nodes, `agents/` is the
  node factory, `registry.py` is what defines an agent, `tools/`, `services/`,
  prompts in `prompts.py`, and the
  ROS boundary is a package split. Renaming working agents ("chat" →
  "general_query_agent") would churn the registry, handover Literal, slot
  map, sticky logic and tests for zero behaviour. New agents get the
  descriptive names (`swiggy` predates the convention; rename it the next
  time its contract changes anyway).

## MCP providers (remote tool servers)

`services/mcp.py` is the registry for remote MCP servers — Swiggy food /
instamart / dineout today; movie tickets, bus tickets, whatever tomorrow.
One frozen `ProviderSpec` per server: name, endpoint URL (+ env override),
`auth_domain` (providers that share a login share a domain — all three Swiggy
servers use domain `"swiggy"`), and an optional legacy token env var.

- **Tokens** live in `~/.langrobo/mcp_tokens.json` (keyed by domain, written
  by `scripts/swiggy_login.py`, 0600); `spec.token_env` overrides when set.
  No token → the provider loads ZERO tools and never touches the network —
  smoke tests and offline boots depend on this.
- **Every loaded tool is wrapped** (`_guard_tool`) with an identical
  name/description/args_schema (KV-cache rule: the LLM-visible schema is part
  of the llama.cpp prompt prefix). At runtime a 401 marks the whole auth
  domain stale, sends exactly one Telegram nudge to the owners, and returns a
  graceful string to the LLM instead of raising; other errors return an honest
  failure string.
- **Reload safety**: tool objects are frozen at process start (build_graph's
  ToolNodes capture the lists at boot), so never-configured → configured needs
  one brain restart. A token *refresh* is only a header mutation — the
  adapters open a fresh MCP session per tool call from the connection dict
  captured at load, so `refresh_tokens_if_changed()` (one `os.stat`, called
  only at MCP-agent node entry) re-arms existing tools with zero KV impact.
- `provider_ok(name)` drives each agent's prompt swap to its
  `*_UNAVAILABLE_NOTE`; `mcp.status()` feeds `/status` on :8090.

## How to add an agent

Three files, and the rest is derived. There is no per-agent node module any
more — `agents/factory.py` builds the node from the spec.

1. Write its system prompt in `prompts.py` (start from CHAT_PROMPT's shape;
   prepend PERSONA for any user-facing agent). Put a `{tools}` placeholder
   where the tool list goes — never hand-write one; `render_tools()` fills it
   from the bound tool set, which is what stops the prompt naming a tool that
   does not exist.
2. Add tools in `tools/`, a named tool set in `tools/__init__.py`
3. Add the name to `agent_ids.py` and one `AgentSpec` to `registry.py`
   (description, examples, prompt, tool set, and the `sticky` / `keep_images` /
   `context` / `mcp_provider` flags). `graph/build.py`, the handover grammar,
   the supervisor's routing table, sticky entry and the rendered tool block all
   follow — no other source file needs an edit.
4. Give it a slot override in `agent_node.py` (`dict(_spec)` for specialists)
   and add it to `EXPECTED_AGENTS` in `tests/test_smoke.py`

`registry.py` asserts at import that it and `agent_ids.py` agree, and
`tests/test_prompt_contract.py` fails if a prompt and its tool set drift.

For an agent backed by a remote MCP server, add step 0: a `ProviderSpec` in
`services/mcp.py` (see "MCP providers" above) and load its tool set in
`tools/__init__.py` via `load_provider_tools`. Then the spec just needs
`mcp_provider=` and `unavailable_note=` — the factory does the `provider_ok`
prompt swap and the token refresh.

## How to add a tool

1. Write it in `tools/` with `@tool`, using `_bridge.get()` for robot I/O
2. Add it to the agent's tool set in `tools/__init__.py`
3. Nothing else — the agent's `== TOOLS ==` block is generated from the tool
   set. Add prompt text only for *policy* the docstring cannot carry.
