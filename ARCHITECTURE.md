# Pi5 Robot Brain — Architecture Guide

How the system works, how the pieces fit together, and how to extend it.

---

## System Overview

```
Mac Mini          llama.cpp at singireddys-mac-mini.local:8080 — Gemma 3n multimodal GGUF (OpenAI-compatible HTTP)
Jetson Orin 8GB   Logitech USB cam · Isaac ROS (SLAM, Nav2, nvblox) · STT · TTS · YOLO · Moondream VLM
Pi 5  [this repo] LangGraph supervisor + agents + micro-ROS agent (ESP32 bridge)
ESP32             4-wheel drive chassis (micro-ROS over WiFi UDP port 8888)
```

The Pi5 receives speech from Jetson, runs the LangGraph decision graph, and publishes
responses back to Jetson (TTS text, Nav2 goals) and to ESP32 (fine movement via micro-ROS).

---

## Data Flows

```
Jetson STT (Whisper small)
    │  /voice/user_input
    ▼
Pi5 LangGraph brain
    │  /voice/robot_speech     → Jetson tts_node (Kokoro) → USB speaker
    │  /vision/query           → Jetson moondream_node
    │  /goal_pose              → Jetson Nav2 → /cmd_vel → Pi5 micro-ROS → ESP32
    │  /cmd_vel (direct)       → Pi5 micro-ROS → ESP32 (fine movement only)
    ↑  /camera/color/image_raw ← Logitech USB cam on Jetson (compressed; cached on Pi5, sent to Gemma when local_agent calls look())
    ↑  /vision/query_result    ← Jetson moondream_node
    ↑  /visual_slam/tracking/odometry ← Jetson Isaac ROS SLAM
```

---

## Package Layout

```
graph_studio.py              LangGraph Studio entry point (langgraph dev)
src/
├── ai_agent/           Brain — LangGraph supervisor + all agents + tools
│   ├── ai_agent/
│   │   ├── agent_node.py        ROS2 entry point (only file that ties ROS2 + graph)
│   │   ├── ros2_bridge.py       All ROS2 I/O — the only other file that imports rclpy
│   │   ├── studio_bridge.py     StubBridge — no-op ROS2Bridge for Studio without hardware
│   │   └── graph/
│   │       ├── state.py         AgentState TypedDict
│   │       ├── graph.py         StateGraph topology
│   │       ├── llm.py           LLM factory (provider-agnostic)
│   │       ├── nodes/
│   │       │   ├── turn_entry.py       Resets loop guard, routes to supervisor
│   │       │   ├── handle_handover.py  Resolves handover: chain vs sticky, loop guard
│   │       │   ├── supervisor.py       Pure router — calls handover(), never speaks
│   │       │   ├── chat.py             General conversation + web search
│   │       │   ├── vision.py           Moondream text VLM (dormant — superseded by local_agent, kept for restore)
│   │       │   ├── local_agent.py      Conversational vision — reasons over real frames via Gemma + look()
│   │       │   ├── navigate.py         Map nav (Nav2) + object nav (VLM + Twist)
│   │       │   ├── status.py           Robot operational state
│   │       │   ├── swiggy.py           Food ordering
│   │       │   └── tracker.py          Delivery tracking + door navigation
│   │       ├── tools/
│   │       │   ├── _bridge.py       Module-level bridge accessor (injected at startup)
│   │       │   ├── handover.py      handover() tool — the routing mechanism
│   │       │   ├── speech.py        speak()
│   │       │   ├── vision.py        query_vision()  — Moondream text query (Jetson)
│   │       │   ├── look.py          look()          — capture frame into conversation as an image (local_agent)
│   │       │   ├── movement.py      move_robot(), navigate_to_pose(), navigate_to_visible_object(), navigate_to_object()
│   │       │   ├── system.py        get_robot_status(), ros2_publish(), set_active_order()
│   │       │   ├── swiggy_mcp.py    Loads Swiggy MCP tools at startup
│   │       │   └── __init__.py      Named tool sets per agent
│   │       └── utils/
│   │           └── message_utils.py  prepare_messages_for_agent(), safe_invoke()
│   └── config/agent_params.yaml
├── robot_brain/        Launch only — micro_ros_agent + agent_node (no chassis_pilot)
└── robot_interfaces/   Custom ROS2 interfaces (FindObjectPose.srv)
```

---

## The One Hard Rule

> **`graph/` is a pure LangGraph zone — zero ROS2 imports.**
> Only `agent_node.py` and `ros2_bridge.py` are allowed to import `rclpy`.

This lets you run and test all graph logic on any machine, no robot needed.

---

## Two Entry Points

The same graph can be driven in two ways — never simultaneously.

| | `ros2 launch` | `langgraph dev` |
|---|---|---|
| Entry file | `agent_node.py` | `graph_studio.py` |
| Input source | `/voice/user_input` ROS2 topic (Jetson STT) | LangGraph Studio browser UI |
| Bridge | `ROS2Bridge` (real hardware) | `ROS2Bridge` if ROS2 sourced, else `StubBridge` |
| State persistence | In-node history list (bounded by `history_turns`) | LangGraph dev server in-memory checkpointer |
| LLM config | `agent_params.yaml` via ROS2 parameters | `.env` via `STUDIO_PROVIDER` / `STUDIO_MODEL` |

**`graph_studio.py` startup sequence:**
1. `load_dotenv()` — reads `.env` for API keys and `STUDIO_*` config
2. `llm_module.configure()` — sets provider/model/key
3. Try `rclpy.init()` + `ROS2Bridge` with background spin thread → real robot control
4. On failure → `StubBridge` (logs tool calls, no ROS2 publishing)
5. `bridge_module.init(bridge)` — tools pick it up at invocation time
6. `graph = build_graph()` — exported for Studio

**`StubBridge` behaviour:**
- `publish_speech()` — logs the text
- `query_vision()` — returns an explanatory string
- `get_frame()` — returns `None` (so `look()` reports "no camera frame" instead of crashing),
  or serves the file at `STUDIO_TEST_IMAGE` when set — lets you test `look()` vision off-robot
- `navigate_to_pose()` / `move_robot()` — logs the command, simulates success
- `call_service()` — raises `TimeoutError` (caught by existing tool handlers)

---

## Thread Model

```
ROS2 spin thread (main)           worker thread (daemon)
        │                                  │
  fills sensor caches               drains input_queue
  puts user text in queue           calls graph.invoke()
  fires delivery poll timer         runs LLM + tools
        │                                  │
        └──── input_queue ─────────────────┘
```

The spin thread never blocks on LLM work. The worker thread never touches ROS2 directly.

---

## Graph Topology

```
START
  │
  ▼
turn_entry  ──► (resets agent_turn_visits, sets always_speak=True)
  │
  ▼ Command(goto="supervisor")
supervisor  ──► supervisor_tools  ──► handle_handover
                                            │
              ┌─────────────────────────────┼──────────────────────────────┐
              ▼         ▼         ▼         ▼         ▼         ▼         ▼
            chat      vision  navigate   status    swiggy   tracker  (any agent)
              │         │         │         │         │         │
           per-agent tool nodes (chat_tools, vision_tools, …)
              │         │         │         │         │         │
              └─────────┴─────────┴─────────┴─────────┴─────────┘
                                    │
                             handle_handover
                          ┌─────────┴──────────┐
                          │                    │
                   chain=True              chain=False
                   or agent silent         agent spoke
                          │                    │
                 Command(goto=next)            END
                 (immediate)             (sticky: next agent
                                          picks up next turn)
```

---

## Conversational Vision — `local_agent`

`local_agent` is a multimodal agent (Gemma 3n via llama.cpp) that reasons over the
**actual camera frame**, not Moondream's flattened text. It owns the visual-conversation
route — it replaces the old Moondream-backed `vision` agent, which stays wired but
unrouted (drop it back into `_registry.AGENTS` to restore).

Why a separate agent instead of attaching frames to every turn: a real image stays in
the conversation, so a follow-up about the *same* scene reasons over the same pixels
instead of re-querying Moondream and getting a fresh, possibly different, text answer.

**Flow:**

```
supervisor → local_agent
    │  (no recent frame in context)
    ├─ look()  →  grabs cached frame, injects it as a HumanMessage image block
    │             (OpenAI-compatible servers won't carry images in tool-role
    │              messages, so look() returns a text ack + a follow-up image msg)
    ▼
Gemma sees the pixels, answers. The frame STAYS in history.
    │
    └─ follow-up ("did he wear spectacles?") reasons over the same image — no re-capture
```

**Frame freshness:** reuse the in-history frame for follow-ups about the same scene;
call `look()` again only for a new/changed view ("look again", "what now"). A hard
staleness timeout (force a fresh look after ~15s) is planned — currently prompt-guided.

### Single master log, projected per-agent

There is one shared conversation log. Each agent is fed a *projection* of it:

| Agent | Projection |
|-------|-----------|
| `local_agent` | image-preserving (`prepare_messages_for_agent(..., keep_images=True)`) |
| every other agent | **image-stripped** text (default — frames collapse to `[Current camera view]`) |

So frames live in the master log but only the multimodal agent pays for them.
`agent_node` persists the full message objects (images included) across turns; trimming
happens **only at a HumanMessage boundary** — append-only within the cap, one reset at
the boundary, never a per-turn front shift (which would break the slot cache below).

### Per-agent llama.cpp slots

`get_llm(agent)` merges the global LLM config with a per-agent override and pins a
llama.cpp KV-cache slot via `extra_body={"id_slot": N}`. `local_agent` can get a
**dedicated slot** so its hot image prefix isn't churned out when other agents hit the
shared server.

**The Mac Mini server (Gemma 3n E4B Q8_0) already provides the cache machinery:**

- `n_parallel = 4` (auto) — **4 slots exist by default**, no `--parallel` flag needed.
  Set `local_agent_slot` in `agent_params.yaml` to `0–3` (`-1` = auto / no pinning).
- `kv_unified = true`, `n_ctx = 131072` per slot — 128k context from a shared KV pool
  (slots do **not** split context here).
- **Prompt cache enabled (8 GB):** idle slots are saved to the prompt cache and restored
  by longest-prefix match. This gives automatic cross-task KV reuse regardless of slot —
  so pinning is **insurance**, not load-bearing.
- **Context checkpoints** (max 32, spacing 256) — KV checkpointed ~every Gemma image.
- `local_agent_model` overrides the model for this agent only (e.g. a multimodal GGUF
  while other agents run a text model).

> **Still verify before relying on the speed win:** the server clearly reuses *text*
> prefixes, but whether it reuses KV *across the image boundary* (vs. re-running the
> mmproj vision encoder) is version-dependent and not shown in the boot logs. Run a
> 2-turn `look()` conversation and check the cached/restored token count on turn 2. The
> intelligence win (real frames in context) holds regardless; only the "no re-prefill"
> speed-up needs this confirmed.

> **GGUF token warning:** this build logs mislabeled `<|tool_response>` / `</s>` control
> tokens. Since routing leans on tool calls (`look`, `handover`, `speak`), watch for
> flaky tool-call parsing or early stops — if seen, suspect the quant/chat template.

---

## Navigation: Two Modes

### Map-based (Nav2 via /goal_pose)

```
navigate_to_pose("kitchen")
  → bridge.publish_goal_pose(x, y, yaw_deg)
  → /goal_pose (PoseStamped, frame=map)
  → Jetson Nav2 plans path using nvblox 3D map
  → Nav2 publishes /cmd_vel
  → Pi5 micro-ROS agent → WiFi UDP 8888 → ESP32 → wheels
```

Obstacle avoidance handled automatically by nvblox + Nav2.

### Object-based (VLM + direct Twist)

```
navigate_to_object("the blue bottle")
  → 360° scan via query_vision() + Moondream
  → bridge.publish_twist() directly to /cmd_vel for each rotation + approach step
  → Pi5 micro-ROS agent → WiFi UDP 8888 → ESP32 → wheels
```

Used when the target is not a named map location. No Nav2 involvement.

### Fine adjustment (direct Twist)

```
move_robot("F:20")      # 20 cm forward
move_robot("L:90")      # 90° left rotate
move_robot("S")         # stop
```

Publishes Twist directly to `/cmd_vel`. For small precise corrections after arriving.

---

## ROS2 Topic Reference

| Topic | Type | Direction | Notes |
|-------|------|-----------|-------|
| `/voice/user_input` | String | Jetson → Pi5 | STT output — triggers graph.invoke() |
| `/voice/robot_speech` | String | Pi5 → Jetson | TTS text for Kokoro |
| `/camera/color/image_raw` | Image | Jetson (Logitech) → Pi5 | Compressed frames — cached on Pi5, sent to Gemma when local_agent calls look() |
| `/vision/query` | String | Pi5 → Jetson | Question for Moondream VLM |
| `/vision/query_result` | String | Jetson → Pi5 | Moondream answer |
| `/visual_slam/tracking/odometry` | Odometry | Jetson → Pi5 | Robot pose from Isaac ROS SLAM |
| `/goal_pose` | PoseStamped | Pi5 → Jetson | Map-based navigation goal for Nav2 |
| `/cmd_vel` | Twist | Pi5 → ESP32 | Wheel velocities via micro-ROS agent |
| `/brain/thinking` | Bool | Pi5 internal | True while LLM running |

**Removed (vs previous architecture):**

| Topic | Reason |
|-------|--------|
| `/movement_cmd` | Replaced by direct Twist to `/cmd_vel` |
| `/vision/objects_3d` | `spatial_node` removed — nvblox handles 3D mapping, Moondream handles object queries |
| `/vision/image_raw` | Replaced by `/camera/color/image_raw` (D555 native topic) |
| `/ir_obstacle` | IR sensor removed — D555 + nvblox handles all obstacle detection |

---

## Tool Reference

| Tool | File | Does |
|------|------|------|
| `handover(next_agent, reason, chain)` | `tools/handover.py` | Routes to another agent |
| `speak(text)` | `tools/speech.py` | Publishes to `/voice/robot_speech` |
| `query_vision(question)` | `tools/vision.py` | Publishes to `/vision/query`, blocks on `/vision/query_result` (Moondream) |
| `look()` | `tools/look.py` | Captures the cached frame into the conversation as an image block (local_agent) |
| `navigate_to_pose(location)` | `tools/movement.py` | Publishes PoseStamped to `/goal_pose` → Jetson Nav2 |
| `navigate_to_visible_object(target)` | `tools/movement.py` | Calls Jetson `/vision/find_object_pose` service → Nav2; falls back to VLM scan |
| `navigate_to_object(target)` | `tools/movement.py` | Fallback: VLM 360° scan + direct Twist approach (no Nav2) |
| `move_robot(command)` | `tools/movement.py` | Fine Twist: `F:20` / `L:90` / `S` — direct to `/cmd_vel` |
| `get_robot_status()` | `tools/system.py` | Calls `/robot/get_status` service |
| `ros2_publish(topic, data)` | `tools/system.py` | Generic String publisher |
| `set_active_order(order_id)` | `tools/system.py` | Stores/clears Swiggy order ID for polling |
| Swiggy MCP tools | `tools/swiggy_mcp.py` | Food ordering via `https://mcp.swiggy.com/food` |

---

## Per-agent Tool Sets

| Agent | Tool Set |
|-------|----------|
| `supervisor` | `handover` |
| `chat` | `CHAT_TOOLS`: `speak`, `query_vision`, `get_robot_status` |
| `local_agent` | `LOCAL_AGENT_TOOLS`: `speak`, `look`, `handover` — multimodal, sees real frames |
| `vision` | `VISION_TOOLS`: `speak`, `query_vision` — dormant (superseded by `local_agent`) |
| `navigate` | `NAVIGATE_TOOLS`: `speak`, `move_robot`, `navigate_to_pose`, `navigate_to_visible_object`, `navigate_to_object`, `query_vision` |
| `status` | `STATUS_TOOLS`: `speak`, `get_robot_status`, `ros2_publish` |
| `swiggy` | Swiggy MCP tools + `speak` |
| `tracker` | Swiggy MCP tools + `navigate_to_pose` + `speak` |

---

## micro-ROS (Pi5 ↔ ESP32)

- **Agent:** `micro_ros_agent` — started automatically by `brain_launch.py`
- **Transport:** WiFi UDP port 8888
- **Install:** built from source in `~/microros_ws` (see INTEGRATION.md § micro-ROS agent)
- **ESP32 subscribes:** `/cmd_vel` (Twist) — wheel motor velocities
- **ESP32 publishes:** nothing (IR sensor removed)

The micro-ROS agent transparently bridges the ROS2 graph on Pi5 to micro-ROS nodes on ESP32.
Nav2 (on Jetson) and LangGraph tools (on Pi5) both publish to `/cmd_vel` — the micro-ROS agent
forwards all Twist messages to ESP32 without any routing logic.

---

## Supervisor Routing

| User says | Routes to |
|-----------|-----------|
| General question, small talk | `chat` |
| "what do you see", "describe", "is there a...", "did he wear...", "is this the real..." | `local_agent` |
| "go to [room]", "find [object]", "move forward" | `navigate` |
| "battery", "status", "what are you doing" | `status` |
| "order food", "search restaurants", "add to cart" | `swiggy` |
| "where's my order", "delivery ETA", "track" | `tracker` |

The supervisor never produces text. Any text alongside a handover call is stripped.

---

## Response Contract & Loop Safety

**Response contract** — every agent follows the same rule:

- The actual answer goes in the agent's **message text**. `agent_node` auto-publishes the
  final AI text to `/voice/robot_speech` (TTS); Studio displays it. An empty final message
  shows as "No data".
- `speak()` is **acknowledgement-only** — use it *before* a slow tool (e.g. "let me look"),
  never to carry the final answer (that empties the message and creates a second TTS path).

**Loop safety** — `handle_handover` prevents runaway routing structurally, independent of
the model:

- **Self-handover** (agent routes to itself) → re-entered once with a hard "answer now,
  do not hand over" nudge; prompts also forbid it.
- **Deterministic loop guard** — per-turn visit counter (`_MAX_VISITS_PER_AGENT = 3`, reset
  each turn by `turn_entry`), applied to **every** agent with no exemptions. On overflow the
  turn ends with a plain fallback message **without another LLM call**.

> Small models (Gemma 3n E4B) over-route — these guards make that safe; routing improves on
> Gemma 12B.

---

## Swiggy + Delivery Flow

```
User: "order biryani"
    │
    ▼  supervisor → handover("swiggy")
swiggy agent: search → browse → confirm → place order
    │
    ├── calls set_active_order(order_id)
    └── handover("tracker", chain=True)
            │
tracker agent: checks status immediately, reports ETA
    │
    └── handover("supervisor")

--- 2 minutes later ---

ROS2 timer fires, reads bridge.active_order_id
    │
    ▼  injects "[SYSTEM] Check if order {id} has been delivered"
supervisor → handover("tracker")
    │
tracker: order delivered?
    ├── YES: speak("Your order has arrived!")
    │         navigate_to_pose("entrance")   ← Nav2 navigates to door
    │         set_active_order(None)
    │         handover("chat")
    └── NO: report new ETA, handover("supervisor")
```

---

## LLM Configuration

Edit `config/agent_params.yaml` — no code changes:

```yaml
agent_node:
  provider: "llamacpp"                              # llamacpp | openai | anthropic | gemini | ollama
  model: "default"                                  # llama.cpp ignores model name
  base_url: "http://singireddys-mac-mini.local:8080/v1"      # Mac Mini llama.cpp
  api_key_env: ""                                   # env var name holding the API key
  max_tokens: 3000

  # Per-agent overrides for the multimodal local_agent:
  local_agent_slot: 0                               # dedicated llama.cpp KV slot (-1 = none; needs --parallel N)
  local_agent_model: ""                             # override model for local_agent only ("" = inherit)
```

Per-agent config is read by `get_llm(agent_name)` in `graph/llm.py` (see
[Conversational Vision](#conversational-vision--local_agent) for slots). Other agents can
be promoted to their own slot/model the same way.

Or override at launch:
```bash
ros2 launch robot_brain brain_launch.py base_url:=http://singireddys-mac-mini.local:8080/v1
```

---

## Adding Named Locations

After building a SLAM map on Jetson, update `agent_params.yaml` with the real coordinates:

```yaml
agent_node:
  locations.kitchen:     [2.5,  1.0,  0.0]     # [x_meters, y_meters, yaw_degrees]
  locations.bedroom:     [-2.0, 2.0, 180.0]
  locations.living_room: [0.0,  3.0,  90.0]
  locations.entrance:    [0.0,  0.0,   0.0]
```

To get coordinates: drive the robot to each location after SLAM is running, then read pose from:
```bash
ros2 topic echo /visual_slam/tracking/odometry --once
```

---

## How to Add a New Agent

1. **Add tools** in `graph/tools/` using `@tool` and `_bridge.get()`
2. **Create the node** in `graph/nodes/` (copy `vision.py` as template)
3. **Add to tool sets** in `graph/tools/__init__.py`
4. **Wire into graph** in `graph/graph.py` — add node + ToolNode, add to `_AGENTS`
5. **Update supervisor prompt** — add new agent name + description of when to route to it

---

## How to Add a New Tool

1. Write it in the appropriate `graph/tools/` file using `@tool` and `_bridge.get()`
2. Add it to the relevant agent's tool set in `graph/tools/__init__.py`
3. Mention it in that agent's system prompt

No other changes needed — the per-agent ToolNode picks it up automatically.
