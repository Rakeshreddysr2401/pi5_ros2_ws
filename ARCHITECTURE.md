# Pi5 Robot Brain — Architecture Guide

How the system works, how the pieces fit together, and how to extend it.

---

## System Overview

```
Mac Mini          llama.cpp server — Gemma 4 or any GGUF (OpenAI-compatible HTTP)
Jetson Orin 8GB   STT · TTS · Camera · YOLO · Moondream VLM  [speech_vision repo]
Pi 5  [this repo] LangGraph supervisor + agents + chassis motor control
ESP32             4-wheel drive chassis (micro-ROS2)
```

The Pi5 receives speech from Jetson, runs the LangGraph decision graph, and publishes
responses back to Jetson (TTS) and to the ESP32 (motor commands).

---

## Package Layout

```
src/
├── ai_agent/           Brain — LangGraph supervisor + all agents + tools
│   ├── ai_agent/
│   │   ├── agent_node.py        ROS2 entry point (only file that ties ROS2 + graph)
│   │   ├── ros2_bridge.py       All ROS2 I/O — the only other file that imports rclpy
│   │   └── graph/
│   │       ├── state.py         AgentState TypedDict
│   │       ├── graph.py         StateGraph topology
│   │       ├── llm.py           LLM factory (provider-agnostic)
│   │       ├── prompts.py       Prompt reference (prompts live inline in each node)
│   │       ├── nodes/
│   │       │   ├── turn_entry.py    Resets loop guard, routes to supervisor
│   │       │   ├── handle_handover.py  Resolves handover: chain vs sticky, loop guard
│   │       │   ├── supervisor.py    Pure router — calls handover(), never speaks
│   │       │   ├── chat.py          General conversation + web search
│   │       │   ├── vision.py        Visual reasoning
│   │       │   ├── navigate.py      Movement + navigation
│   │       │   ├── status.py        Robot operational state
│   │       │   ├── swiggy.py        Food ordering
│   │       │   └── tracker.py       Delivery tracking + door navigation
│   │       ├── tools/
│   │       │   ├── _bridge.py       Module-level bridge accessor (injected at startup)
│   │       │   ├── handover.py      handover() tool — the routing mechanism
│   │       │   ├── speech.py        speak()
│   │       │   ├── vision.py        query_vision(), get_detected_objects()
│   │       │   ├── movement.py      move_robot(), navigate_to()
│   │       │   ├── system.py        get_robot_status(), ros2_publish(), set_active_order()
│   │       │   ├── swiggy_mcp.py    Loads Swiggy MCP tools at startup
│   │       │   └── __init__.py      Named tool sets per agent
│   │       └── utils/
│   │           └── message_utils.py  prepare_messages_for_agent(), safe_invoke()
│   └── config/agent_params.yaml
├── robot_brain/        Muscles — chassis_pilot node (motor control)
└── robot_interfaces/   Custom ROS2 messages (RobotStatus.msg)
```

---

## The One Hard Rule

> **`graph/` is a pure LangGraph zone — zero ROS2 imports.**
> Only `agent_node.py` and `ros2_bridge.py` are allowed to import `rclpy`.

This lets you run and test all graph logic on any machine, no robot needed.

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

## State

```python
class AgentState(TypedDict):
    messages:          Annotated[list, add_messages]  # full conversation, managed by LangGraph
    active_agent:      str                            # which agent handled the last turn
    agent_turn_visits: dict                           # loop guard: visits per agent per turn
    always_speak:      Optional[bool]                 # True = agents always call speak()
```

`MemorySaver` is used as the checkpointer — conversation history persists across turns
within a session and resets when the node restarts.

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

## How Handover Works

Every agent (except `turn_entry` and `handle_handover`) has the `handover` tool.
When an agent calls `handover(next_agent, reason, chain)`:

1. The tool returns JSON: `{"next_agent": "...", "reason": "...", "chain": bool}`
2. The agent's ToolNode executes it and adds a `ToolMessage` to state
3. `_route_after_tools()` detects the handover ToolMessage → routes to `handle_handover`
4. `handle_handover` decides:
   - **chain=True or agent was silent** → `Command(goto=next_agent)` — next agent responds *this turn*
   - **chain=False and agent spoke** → state update + `END` — next agent picks up *next turn* (sticky)

The **loop guard** tracks `agent_turn_visits`. If any agent is visited more than 3 times per turn,
it breaks the cycle and redirects to `chat`.

**Message hygiene:** `prepare_messages_for_agent()` strips handover `ToolMessages` and empty
routing `AIMessages` before each LLM call so agents see a clean conversation history.

---

## Supervisor Routing

The supervisor's system prompt maps intents to agents:

| User says | Routes to |
|-----------|-----------|
| General question, web search, small talk | `chat` |
| "what do you see", "describe", "is there a..." | `vision` |
| "go to", "move forward", "find the chair" | `navigate` |
| "battery", "status", "what are you doing" | `status` |
| "order food", "search restaurants", "add to cart" | `swiggy` |
| "where's my order", "delivery ETA", "track" | `tracker` |

The supervisor never produces text. Any text alongside a handover call is stripped.

---

## Swiggy + Delivery Flow

```
User: "order biryani"
    │
    ▼  supervisor → handover("swiggy")
swiggy agent: search → browse → confirm → place order
    │
    ├── calls set_active_order(order_id)   [stores on bridge for polling timer]
    └── handover("tracker", chain=True)
            │
            ▼
tracker agent: checks status immediately, reports ETA
    │
    └── handover("supervisor", reason="tracking_done")

--- 2 minutes later ---

ROS2 timer fires, reads bridge.active_order_id
    │
    ▼  injects "[SYSTEM] Check if order {id} has been delivered" into input_queue
supervisor → handover("tracker")
    │
tracker: order delivered?
    ├── YES: speak("Your order has arrived!")
    │         navigate_to("door")
    │         set_active_order(None)       [clears polling]
    │         handover("chat", chain=True)
    │              │
    │         chat: greets delivery / assists user
    └── NO: report new ETA, handover("supervisor")
```

---

## Tool Reference

| Tool | File | Does |
|------|------|------|
| `handover(next_agent, reason, chain)` | `tools/handover.py` | Routes to another agent |
| `speak(text)` | `tools/speech.py` | Publishes to `/voice/robot_speech` |
| `query_vision(question)` | `tools/vision.py` | Publishes to `/vision/query`, blocks on `/vision/query_result` |
| `get_detected_objects()` | `tools/vision.py` | Returns cached YOLO detections |
| `move_robot(command)` | `tools/movement.py` | Sends `F:20` / `L:90` / `S` to chassis_pilot |
| `navigate_to(target)` | `tools/movement.py` | 360° scan + autonomous approach |
| `get_robot_status()` | `tools/system.py` | Calls `/robot/get_status` service |
| `ros2_publish(topic, data)` | `tools/system.py` | Generic String publisher |
| `set_active_order(order_id)` | `tools/system.py` | Stores/clears Swiggy order ID for polling |
| Swiggy MCP tools | `tools/swiggy_mcp.py` | Food ordering via `https://mcp.swiggy.com/food` |

---

## ROS2 Topic Reference

| Topic | Direction | Type | Purpose |
|-------|-----------|------|---------|
| `/voice/user_input` | Jetson → Pi5 | `String` | STT output — triggers graph.invoke() |
| `/vision/image_raw` | Jetson → Pi5 | `Image` | Camera frames — cached, attached to LLM calls |
| `/vision/objects_3d` | Jetson → Pi5 | `String` (JSON) | YOLO + depth detections |
| `/vision/query` | Pi5 → Jetson | `String` | Question for Moondream VLM |
| `/vision/query_result` | Jetson → Pi5 | `String` | Moondream answer |
| `/voice/robot_speech` | Pi5 → Jetson | `String` | TTS text |
| `/movement_cmd` | Pi5 internal | `String` | `F:20`, `L:90`, `S` — LangGraph → chassis_pilot |
| `/cmd_vel` | Pi5 → ESP32 | `Twist` | Wheel velocities via micro-ROS2 WiFi UDP. Nav2 publishes here directly in future |
| `/ir_obstacle` | ESP32 → Pi5 | `Bool` | IR sensor — `true` = obstacle. chassis_pilot hard-stops on this |
| `/servo_angle` | Pi5 → ESP32 | `UInt16` | Head servo angle 0–180° |
| `/brain/thinking` | Pi5 internal | `Bool` | `true` while LLM is running |
| `/robot/get_status` | Pi5 service | `Trigger` | Battery + hardware state |

### micro-ROS2 Transport

ESP32 connects to Pi5 over **WiFi UDP** (port 8888).
Pi5 runs `micro_ros_agent udp4 --port 8888` (started automatically by `brain_launch.py`).
All topics above prefixed `/cmd_vel`, `/ir_obstacle`, `/servo_angle` are bridged through this agent.

For wiring, flashing, and troubleshooting see [INTEGRATION.md](INTEGRATION.md).

---

## How to Add a New Agent

Example: adding an `arm` agent for robot arm control.

**1. Add tools** (`graph/tools/system.py` or new file):
```python
@tool
def move_arm(joint: str, angle: float) -> str:
    """Move a robot arm joint to the given angle in degrees."""
    _bridge.get().publish_to_topic("/arm/joint_goal", f'{{"joint": "{joint}", "angle": {angle}}}')
    return f"Moving {joint} to {angle}°"
```

**2. Add to tool sets** (`graph/tools/__init__.py`):
```python
from .system import ..., move_arm

ARM_TOOLS = [speak, move_arm, ros2_publish, handover]
```

**3. Create the node** (`graph/nodes/arm.py`):
```python
from langchain_core.messages import SystemMessage
from ..llm import get_llm
from ..state import AgentState
from ..tools import ARM_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke
import logging

logger = logging.getLogger(__name__)

_PROMPT = """You control the robot arm. Use move_arm() for joint control..."""

def arm_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(ARM_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "arm"}
```

**4. Wire into graph** (`graph/graph.py`):
```python
from .nodes.arm import arm_node
from .tools import ARM_TOOLS

# Add to _AGENTS list
_AGENTS = ["supervisor", "chat", "vision", "navigate", "status", "swiggy", "tracker", "arm"]

# Add nodes
builder.add_node("arm", arm_node)
builder.add_node("arm_tools", ToolNode(tools=ARM_TOOLS))
```

**5. Update supervisor prompt** (`graph/nodes/supervisor.py`):
```python
# Add to _PROMPT:
# - "arm": robot arm control, joint movement, pick and place
```

---

## How to Add a New Tool

1. Write it in the appropriate `graph/tools/` file using `@tool` and `_bridge.get()`
2. Add it to the relevant agent's tool set in `graph/tools/__init__.py`
3. Mention it in that agent's system prompt

No other changes needed — the per-agent ToolNode picks it up automatically.

---

## How to Change the LLM

Edit `config/agent_params.yaml` — no code changes:

```yaml
agent_node:
  provider: "llamacpp"                        # llamacpp | openai | anthropic | gemini | ollama
  model: "default"                            # llama.cpp ignores model name
  base_url: "http://192.168.31.24:8080/v1"    # your LLM server IP
  api_key_env: ""                             # env var name holding the API key
  max_tokens: 300
```

Or override at launch:
```bash
ros2 launch robot_brain brain_launch.py base_url:=http://192.168.1.50:8080/v1 provider:=openai
```
