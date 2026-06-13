# Pi5 Robot Brain — Quick Start

The **brain and motor bridge** of a distributed home assistant robot.
Pi5 handles all reasoning (LangGraph + LLM) and routes motor commands via micro-ROS.
Jetson Orin handles perception (STT, TTS, camera, YOLO, Moondream, SLAM, Nav2).

```
Mac Mini  ──────  llama.cpp at singireddys-mac-mini.local:8080  (OpenAI-compatible HTTP)
Jetson    ──────  Isaac ROS (SLAM, Nav2, nvblox) · STT · TTS · YOLO · Moondream
Pi 5      ──────  THIS REPO — LangGraph brain + micro-ROS agent (ESP32 bridge)
ESP32     ──────  4-wheel drive chassis (micro-ROS over WiFi UDP)
```

---

## Prerequisites

### On the Pi5 (Ubuntu 24.04, ROS2 Jazzy)

```bash
# ROS2 Jazzy base (no desktop — saves ~400 MB RAM on Pi5; no GUI needed)
sudo apt install ros-jazzy-ros-base
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc

# ROS2 package dependencies (declared in package.xml, resolved by rosdep)
sudo rosdep init   # skip if already done
rosdep update
cd ~/pi5_ros2_ws
rosdep install --from-paths src --ignore-src -r -y

# Python dependencies — pinned versions for reproducible installs
pip install -r requirements.txt

# micro-ROS agent — built once in a separate workspace, sourced at shell startup
# See INTEGRATION.md § micro-ROS agent setup

# Build the workspace
colcon build --symlink-install
source install/setup.bash
```

### On the Jetson Orin Nano (JetPack 7.2, ROS2 Jazzy)

Start both containers before the Pi5:
```bash
# Container 1: Isaac ROS (SLAM, Nav2, nvblox)
cd ~/robot && docker compose up isaac_ros

# Container 2: AI stack (STT, TTS, YOLO, Moondream)
cd ~/robot && docker compose up ai_stack
```

See `SETUP.md` (Jetson repo) for full container build and launch instructions.

### LLM server (Mac Mini)

```bash
# llama.cpp — serves any GGUF model on the local network
./llama-server -m your-model.gguf --port 8080 -ngl 99
# Access via http://singireddys-mac-mini.local:8080/v1
```

### ESP32 firmware

See [INTEGRATION.md](INTEGRATION.md) for wiring, Arduino IDE setup, and flashing instructions.

---

## Environment Variables

Copy `example.env` and fill in what you need:

```bash
cp example.env .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | If using OpenAI | Cloud LLM API key |
| `ANTHROPIC_API_KEY` | If using Anthropic | Cloud LLM API key |
| `GOOGLE_API_KEY` | If using Gemini | Cloud LLM API key |
| `STUDIO_PROVIDER` | No | LLM provider for Studio (default `openai`) |
| `STUDIO_MODEL` | No | Model for Studio (default `gpt-4o-mini`) |
| `STUDIO_BASE_URL` | No | Custom base URL for Studio (e.g. llama.cpp endpoint) |
| `STUDIO_MAX_TOKENS` | No | Max tokens for Studio LLM (default `3000`) |
| `SWIGGY_FOOD_MCP_URL` | No | Defaults to `https://mcp.swiggy.com/food` |
| `SWIGGY_ACCESS_TOKEN` | No | Bearer token for Swiggy auth |
| `TAVILY_API_KEY` | No | Enables web search in the chat agent |
| `ROS_DOMAIN_ID` | No | Must match Jetson (default `0`) |

---

## Configuration

Edit `src/ai_agent/config/agent_params.yaml`:

```yaml
agent_node:
  provider: "llamacpp"                              # llamacpp | openai | anthropic | gemini | ollama
  base_url: "http://singireddys-mac-mini.local:8080/v1"      # Mac Mini llama.cpp endpoint
  max_tokens: 3000
  use_vision: true
  # Named map locations (x, y, yaw_deg) in SLAM map frame
  # Update these after building a SLAM map on Jetson
  locations.kitchen:     [2.5,  1.0,  0.0]
  locations.living_room: [0.0,  3.0, 90.0]
  locations.bedroom:     [-2.0, 2.0, 180.0]
  locations.entrance:    [0.0,  0.0,  0.0]
```

---

## Launch

### Normal robot launch (voice → ROS2 → robot)

```bash
# Full system (micro-ROS agent + LangGraph brain)
ros2 launch robot_brain brain_launch.py

# Override LLM endpoint
ros2 launch robot_brain brain_launch.py base_url:=http://singireddys-mac-mini.local:8080/v1

# Brain only (no micro-ROS agent — for testing without ESP32)
ros2 launch ai_agent agent.launch.py
```

**Start order:**
1. **Mac Mini:** `./llama-server -m model.gguf --port 8080 -ngl 99`
2. **Jetson:** `docker compose up` (both containers)
3. **Pi5:** `ros2 launch robot_brain brain_launch.py`
4. **ESP32:** power on — auto-connects to Pi5 micro-ROS agent over WiFi

### LangGraph Studio (browser UI → ROS2 → robot)

Chat with the robot from the Studio visual debugger. See the full graph, step through routing decisions, and trigger real robot actions from your browser.

```bash
# On Pi5 — starts micro-ROS agent (background) + Studio (foreground) together
cd ~/ros2_ws
./dev.sh
```

Make it executable first (one time only):
```bash
chmod +x ~/ros2_ws/dev.sh
```

Then:
1. Power on the ESP32 — it auto-connects over WiFi UDP
2. Open [LangGraph Studio](https://smith.langchain.com/studio) → connect to `http://<pi5-ip>:2024`
3. Ctrl+C stops both processes cleanly

> **Note:** Do not run `langgraph dev` and `ros2 launch` simultaneously — both publish to the same ROS2 topics.

Without ROS2 sourced, `langgraph dev` still works: chat and Swiggy tools function normally; movement and vision tools log instead of publishing.

For full wiring, flashing, and troubleshooting see [INTEGRATION.md](INTEGRATION.md).

---

## What Happens at Runtime

```
User speaks
    │
    ▼  /voice/user_input (String)    [Jetson STT → Pi5]
agent_node (ROS2 spin thread)
    │  puts text + cached camera frame into input_queue
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
               ┌────────────────────┼────────────────────┐
               ▼                    ▼                    ▼
    /voice/robot_speech         /goal_pose           /cmd_vel
    (Jetson Kokoro TTS)     (Jetson Nav2 → ESP32) (Pi5 → ESP32 direct)
```

Every 2 minutes a ROS2 timer checks if a Swiggy order is active.
If the order arrives, the tracker agent speaks, navigates to the door via Nav2, then hands off to chat.

---

## Packages

| Package | Purpose |
|---------|---------|
| `ai_agent` | LangGraph supervisor + 7 agents + all tools |
| `robot_brain` | Launch only — starts micro-ROS agent + agent_node |
| `robot_interfaces` | Custom `FindObjectPose.srv` |

---

## Agents

| Agent | Handles | Key Tools |
|-------|---------|-----------|
| `supervisor` | Routes every request — never speaks | `handover` |
| `chat` | General questions, small talk | `speak`, `query_vision`, `get_robot_status` |
| `vision` | What the robot sees | `speak`, `query_vision` |
| `navigate` | Movement, go-to rooms, find objects | `speak`, `navigate_to_pose`, `navigate_to_visible_object`, `navigate_to_object`, `move_robot`, `query_vision` |
| `status` | Battery, hardware, operational state | `speak`, `get_robot_status`, `ros2_publish` |
| `swiggy` | Food ordering, cart, place orders | Swiggy MCP tools |
| `tracker` | Delivery tracking, door navigation on arrival | Swiggy MCP tools, `navigate_to_pose` |

For full architecture details see [ARCHITECTURE.md](ARCHITECTURE.md).
