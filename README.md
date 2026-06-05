# Pi5 Robot Brain — Quick Start

The **brain and muscles** of a distributed home assistant robot.
The Pi5 handles all reasoning (LangGraph + LLM) and motor control.
The Jetson Orin handles sensing (STT, camera, YOLO, Moondream TTS).

```
Mac Mini  ──────  llama.cpp / LLM server  (OpenAI-compatible HTTP)
Jetson    ──────  STT · TTS · Camera · YOLO · Moondream VLM
Pi 5      ──────  THIS REPO — LangGraph brain + chassis control
ESP32     ──────  4-wheel drive chassis (micro-ROS2)
```

---

## Prerequisites

### On the Pi5

```bash
# ROS2 Humble (or Iron)
sudo apt install ros-humble-desktop

# micro-ROS2 agent (bridges Pi5 ↔ ESP32 over WiFi UDP)
sudo apt install ros-$ROS_DISTRO-micro-ros-agent

# Python dependencies
pip install langgraph langchain-core langchain-openai langchain-anthropic \
            langchain-google-genai langchain-ollama langchain-community \
            langchain-mcp-adapters opencv-python-headless cv-bridge

# Build the workspace
cd ~/pi5_ros2_ws
colcon build --symlink-install
source install/setup.bash
```

### On the Jetson

See [speech_vision repo](https://github.com/Rakeshreddysr2401/speech_vision) — launch it before starting the Pi5.

### LLM server (Mac Mini or any machine on the network)

```bash
# llama.cpp example
./llama-server -m your-model.gguf --port 8080 -ngl 99
```

### ESP32 firmware

See [INTEGRATION.md](INTEGRATION.md) for wiring, Arduino IDE setup, and flashing instructions.

---

## Environment Variables

Copy `example.env` and fill in what you need:

```bash
cp example.env .env
# then source it, or export variables individually
```

| Variable | Required | Description |
|----------|----------|-------------|
| `SWIGGY_FOOD_MCP_URL` | No | Defaults to `https://mcp.swiggy.com/food` |
| `SWIGGY_ACCESS_TOKEN` | No | Bearer token for Swiggy auth |
| `TAVILY_API_KEY` | No | Enables web search in the chat agent |
| `OPENAI_API_KEY` | If using OpenAI | Cloud LLM API key |
| `ANTHROPIC_API_KEY` | If using Anthropic | Cloud LLM API key |
| `GOOGLE_API_KEY` | If using Gemini | Cloud LLM API key |
| `ROS_DOMAIN_ID` | No | Must match Jetson (default `0`) |

---

## Configuration

Edit `src/ai_agent/config/agent_params.yaml` to set your LLM provider and Mac Mini IP:

```yaml
agent_node:
  provider: "llamacpp"                      # llamacpp | openai | anthropic | gemini | ollama
  base_url: "http://192.168.31.24:8080/v1"  # your Mac Mini IP
  max_tokens: 300
  use_vision: true
```

---

## Launch

```bash
# Full system (brain + chassis pilot + micro-ROS2 agent)
ros2 launch robot_brain brain_launch.py

# Custom LLM server IP
ros2 launch robot_brain brain_launch.py base_url:=http://192.168.1.50:8080/v1

# Brain only (no motor control)
ros2 launch ai_agent agent.launch.py
```

**Start order:**
1. Mac Mini: `./llama-server -m model.gguf --port 8080 -ngl 99`
2. Jetson: `ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant`
3. Pi5: `ros2 launch robot_brain brain_launch.py`
4. ESP32: power on — auto-connects to Pi5 micro-ROS2 agent over WiFi

For full wiring, flashing and troubleshooting details see [INTEGRATION.md](INTEGRATION.md).

---

## What Happens at Runtime

```
User speaks
    │
    ▼  /voice/user_input (String)
agent_node (ROS2 spin thread)
    │  puts text into input_queue
    ▼
worker thread
    │  graph.invoke()
    ▼
turn_entry  ──►  supervisor (routes via handover)
                     │
          ┌──────────┼──────────┬──────────┬──────────┬──────────┐
          ▼          ▼          ▼          ▼          ▼          ▼
        chat       vision    navigate   status     swiggy    tracker
          │          │          │          │          │          │
        tools      tools      tools      tools      tools      tools
          │          │          │          │          │          │
          └──────────┴──────────┴──────────┴──────────┴──────────┘
                                    │
                             handle_handover
                          (chain or sticky next turn)
                                    │
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
           /voice/robot_speech         /movement_cmd
           (Jetson speaks)             (chassis_pilot → ESP32)
```

Every 2 minutes a ROS2 timer checks if a Swiggy order is active.
If the order arrives, the tracker agent speaks, drives the robot to the door, then hands off to chat.

---

## Packages

| Package | Purpose |
|---------|---------|
| `ai_agent` | LangGraph supervisor + 7 agents + all tools |
| `robot_brain` | `chassis_pilot` node — motor control, visual servoing |
| `robot_interfaces` | Custom `RobotStatus.msg` shared with Jetson |

---

## Agents

| Agent | Handles | Tools |
|-------|---------|-------|
| `supervisor` | Routes every request — never speaks | `handover` |
| `chat` | General questions, web search, small talk | `speak`, `query_vision`, `get_detected_objects`, `get_robot_status`, `tavily_search`*, `handover` |
| `vision` | What the robot sees, object detection | `speak`, `query_vision`, `get_detected_objects`, `handover` |
| `navigate` | Movement, go-to, find-and-approach | `speak`, `move_robot`, `navigate_to`, `query_vision`, `get_detected_objects`, `ros2_publish`, `handover` |
| `status` | Battery, hardware, operational state | `speak`, `get_robot_status`, `ros2_publish`, `handover` |
| `swiggy` | Food ordering, cart, place orders | Swiggy MCP tools*, `handover` |
| `tracker` | Delivery tracking, door navigation on arrival | Swiggy MCP tools*, `set_active_order`, `speak`, `navigate_to`, `handover` |

`*` = requires env var / MCP server

For full architecture details see [ARCHITECTURE.md](ARCHITECTURE.md).
