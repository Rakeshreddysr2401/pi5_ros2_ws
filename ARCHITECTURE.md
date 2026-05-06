# Pi5 Robot Brain — Architecture Guide

How the system works and how to extend it.

---

## System Overview

This workspace runs on a **Raspberry Pi 5** and acts as the brain of a distributed home assistant robot. Three other devices handle the rest:

```
Mac Mini          llama.cpp server  Gemma 4 (OpenAI-compatible HTTP)
Jetson Orin 8GB   STT, TTS, Camera, YOLO, Moondream VLM
Pi 5  [this repo] LangGraph brain, chassis motor control
ESP32             4-wheel drive chassis (micro-ROS2)
```

The Pi5 listens to what the user says (from Jetson), thinks using Gemma 4 (on Mac Mini), and responds or moves accordingly (back to Jetson for speech, or to ESP32 for motors).

---

## Package Layout

```
src/
├── ai_agent/           Brain — LangGraph + LLM logic
├── robot_brain/        Muscles — motor control (chassis_pilot)
└── robot_interfaces/   Shared ROS2 message definitions
```

---

## How the Two Boundary Files Work

There is one hard rule in this codebase:

> **`graph/` is a pure LangGraph zone — zero ROS2 imports.**
> Only `agent_node.py` and `ros2_bridge.py` are allowed to import `rclpy`.

This means you can run and test all graph logic on any machine without a robot attached.

### `ros2_bridge.py` — The ROS2 adapter

This file is the only connection between ROS2 and the rest of the code.
Everything the graph layer needs from the robot goes through here.

It has three sections:

| Section | ROS2 concept | When to use |
|---|---|---|
| **Topics** | `pub` / `sub` | Streaming sensor data, fire-and-forget commands |
| **Services** | `client.call_async()` | Request a value and wait for one reply |
| **Actions** | `ActionClient.send_goal_async()` | Start a long task and wait for it to finish |

Sensor data (camera frames, detected objects) is **cached** on arrival so tools can read it instantly without waiting for a message.

The vision query is a special case: the bridge publishes a question to `/vision/query` and **blocks** until Jetson's Moondream VLM replies on `/vision/query_result`.

### `agent_node.py` — The thin ROS2 node

This file does five things and nothing more:

1. Read ROS2 parameters (LLM provider, base URL, etc.)
2. Create the bridge and inject it into the graph layer
3. Configure the LLM factory
4. Build the graph
5. Run a **worker thread** that drains the input queue and invokes the graph

The ROS2 spin thread and the LangGraph worker thread are kept separate on purpose. The spin thread only fills caches and puts messages on the queue. All LLM work and tool execution happens in the worker thread so the spin thread is never blocked.

---

## How the Graph Works

### State

```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]   # full conversation
    intent:   Optional[Literal["chat", "vision", "navigate", "status"]]
```

`intent` is set by the router and used to route back to the right node after tool calls.

### Topology

```
user speaks
    │
    ▼
/voice/user_input  ──►  agent_node  ──►  queue
                                              │
                                        worker thread
                                              │
                                         graph.invoke()
                                              │
                                    ┌─────────▼─────────┐
                                    │    router_node     │  sets state["intent"]
                                    └─────────┬─────────┘
                         ┌───────────┬────────┴────────┬────────────┐
                         ▼           ▼                  ▼            ▼
                      chat        vision            navigate      status
                      node         node              node          node
                         │           │                  │            │
                    (no tools)  vision_tools    navigator_tools  status_tools
                         │           │                  │            │
                         └─────┬─────┘                  └─────┬──────┘
                               │ tool_calls?                   │ tool_calls?
                               ▼ YES                           ▼ YES
                           ToolNode  ◄────────────────────  ToolNode
                               │ routes back via intent
                               ▼ NO
                              END
                               │
                    agent_node publishes response
                               │
                               ▼
                    /voice/robot_speech  ──►  Jetson TTS
```

### Router

The router uses a two-stage approach to keep latency low:

1. **Keyword scan** (no LLM call) — checks the message against known word lists for each intent
2. **LLM fallback** — only called for messages that don't match any keywords

### Nodes

Each node is a pure function: it takes `AgentState`, binds its tool set to the LLM, calls the LLM once, and returns `{"messages": [response]}`. That is all.

| Node | Tool set | Handles |
|---|---|---|
| `chat` | none | greetings, general questions, small talk |
| `vision` | speak, query_vision, get_detected_objects | "what do you see?", object descriptions |
| `navigate` | speak, move_robot, navigate_to, query_vision, get_detected_objects, ros2_publish | movement, "go to X", "find X" |
| `status` | speak, get_robot_status, ros2_publish | battery, hardware state |

### Tools

Tools call `_bridge.get()` to get the bridge instance. They never import `rclpy` directly.

| Tool | Does |
|---|---|
| `speak(text)` | Publishes to `/voice/robot_speech` immediately |
| `query_vision(question)` | Asks Jetson's Moondream, blocks until answer |
| `get_detected_objects()` | Returns cached YOLO detections as formatted text |
| `move_robot(command)` | Sends `F:20` / `L:90` etc. to chassis_pilot, blocks until done |
| `navigate_to(target)` | Scans 360° then approaches — fully autonomous |
| `get_robot_status()` | Calls `/robot/get_status` ROS2 service (graceful fallback) |
| `ros2_publish(topic, data)` | Publishes a String to any topic (arm, gripper, etc.) |

### `_bridge.py` — the glue

```python
# graph/tools/_bridge.py
_instance = None

def init(bridge): ...   # called once by agent_node
def get():         ...  # called by every tool
```

This is how tools reach the bridge without importing ROS2 types. `agent_node.py` calls `bridge_module.init(bridge)` once at startup. Every tool calls `_bridge.get()` at runtime.

---

## How to Extend

### Add a new tool

1. Create or open the right file in `graph/tools/`:
   - Speech → `speech.py`
   - Vision → `vision.py`
   - Movement → `movement.py`
   - Hardware / system → `system.py`

2. Write the tool function:

```python
# graph/tools/system.py
from langchain_core.tools import tool
from . import _bridge

@tool
def save_waypoint(label: str) -> str:
    """Save the robot's current position as a named waypoint for later navigation."""
    from custom_srvs.srv import SaveWaypoint
    req = SaveWaypoint.Request()
    req.label = label
    resp = _bridge.get().call_service("/map/save_waypoint", SaveWaypoint, req, timeout=5.0)
    return f"Waypoint '{label}' saved" if resp.success else "Save failed"
```

3. Add it to `graph/tools/__init__.py`:

```python
from .system import get_robot_status, ros2_publish, save_waypoint

status_tools    = [speak, get_robot_status, ros2_publish, save_waypoint]
all_tools       = [..., save_waypoint]
```

4. Update the system prompt in `graph/prompts.py` to tell the LLM the tool exists.

---

### Add a new intent and node

Example: you want to add an `arm` intent for controlling a robot arm.

**1. Add the intent to state** (`graph/state.py`):

```python
intent: Optional[Literal["chat", "vision", "navigate", "status", "arm"]]
```

**2. Add the prompt** (`graph/prompts.py`):

```python
arm_prompt = """\
You control the robot arm.
  move_arm(joint, angle) — move a joint to a specific angle
  ros2_publish(topic, data) — raw command to arm hardware
"""
```

**3. Create the node** (`graph/nodes/arm.py`):

```python
from langchain_core.messages import SystemMessage
from ..llm import get_llm
from ..prompts import arm_prompt
from ..state import AgentState
from ..tools import arm_tools

def arm_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(arm_tools)
    response = llm.invoke([SystemMessage(content=arm_prompt)] + state["messages"])
    return {"messages": [response]}
```

**4. Wire it into the graph** (`graph/graph.py`):

```python
from .nodes.arm import arm_node

builder.add_node("arm", arm_node)

# add to router routing
builder.add_conditional_edges("router", _route_by_intent, {
    ...,
    "arm": "arm",
})

# add tool loop
builder.add_conditional_edges("arm", _route_to_tools_or_end, {"tools": "tools", END: END})

# add back-routing after tools
builder.add_conditional_edges("tools", _route_after_tools, {
    ...,
    "arm": "arm",
})
```

**5. Add keywords to router** (`graph/nodes/router.py`):

```python
_ARM_KW = {"arm", "lift", "grab", "gripper", "reach", "pick up", "put down"}

if any(kw in text for kw in _ARM_KW):
    return {"intent": "arm"}
```

---

### Add a ROS2 service call

```python
# In any tool file — just call the bridge
from std_srvs.srv import Trigger
resp = _bridge.get().call_service("/my_service", Trigger, Trigger.Request(), timeout=5.0)
```

The bridge creates the service client lazily on the first call and caches it.

---

### Add a ROS2 action call

```python
# In any tool file
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped

goal = NavigateToPose.Goal()
goal.pose = PoseStamped()
goal.pose.pose.position.x = 2.0

def on_feedback(feedback_msg):
    dist = feedback_msg.feedback.distance_remaining
    _bridge.get().publish_speech(f"{dist:.1f} metres remaining")

result = _bridge.get().send_action(
    "/navigate_to_pose",
    NavigateToPose,
    goal,
    timeout=120.0,
    feedback_cb=on_feedback,
)
```

---

### Change the LLM

Edit `config/agent_params.yaml` — no code changes needed:

```yaml
agent_node:
  provider: "llamacpp"           # or: openai | anthropic | gemini | ollama
  base_url: "http://X.X.X.X:8080/v1"   # Mac Mini IP
  model:    "default"            # llama.cpp ignores this field
  api_key_env: ""                # empty = no key needed
```

Or pass at launch time:

```bash
ros2 launch robot_brain brain_launch.py base_url:=http://192.168.1.50:8080/v1
```

---

## ROS2 Topic Reference

| Topic | Direction | Type | Purpose |
|---|---|---|---|
| `/voice/user_input` | Jetson → Pi5 | `String` | User speech text (STT output) |
| `/vision/image_raw` | Jetson → Pi5 | `Image` | Camera frames (cached for LLM) |
| `/vision/objects_3d` | Jetson → Pi5 | `String` | YOLO detections as JSON |
| `/vision/query` | Pi5 → Jetson | `String` | Question for Moondream VLM |
| `/vision/query_result` | Jetson → Pi5 | `String` | Moondream VLM answer |
| `/voice/robot_speech` | Pi5 → Jetson | `String` | TTS text to speak |
| `/movement_cmd` | internal | `String` | `F:20`, `L:90`, `S` etc. |
| `/cmd_vel` | Pi5 → ESP32 | `Twist` | Wheel velocity commands |
| `/brain/thinking` | Pi5 internal | `Bool` | `true` while LLM is running |

---

## Launch

```bash
# Default (Mac Mini at 192.168.1.100)
ros2 launch robot_brain brain_launch.py

# Custom Mac Mini IP
ros2 launch robot_brain brain_launch.py base_url:=http://192.168.1.50:8080/v1

# Brain only (no chassis pilot)
ros2 launch ai_agent agent.launch.py base_url:=http://192.168.1.50:8080/v1
```
