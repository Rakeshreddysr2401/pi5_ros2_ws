# LangRobo — Pi5 Robot Brain

The **brain** of a distributed home assistant robot ("Rakhi"). The Pi5 runs all
reasoning (LangGraph multi-agent supervisor + LLM) and bridges motor commands to
the chassis; the Jetson handles perception and speech I/O.

```
Mac Mini  ──────  llama.cpp — Gemma multimodal GGUF (OpenAI-compatible HTTP)
Jetson    ──────  USB cam · STT (Whisper) · TTS (Kokoro) · YOLOv8n target_node
Pi 5      ──────  THIS REPO — LangGraph brain + services + micro-ROS agent
ESP32     ──────  4-wheel drive chassis (micro-ROS over WiFi UDP 8888)
```

**Docs:** [HOW_IT_WORKS.md](HOW_IT_WORKS.md) — end-to-end walkthrough (start here) ·
[INTEGRATION_GAPS.md](INTEGRATION_GAPS.md) — **what this brain asks of the rover that the rover does not answer** ·
[ARCHITECTURE.md](ARCHITECTURE.md) — reference: layout, rules, contracts ·
[OPERATIONS.md](OPERATIONS.md) — deploy, systemd, health API, troubleshooting ·
[TELEGRAM.md](TELEGRAM.md) — chat with the robot from your phone: setup + usage ·
[PRODUCT.md](PRODUCT.md) — product thesis + roadmap

---

## Repo layout

```
src/
├── langrobo_core/     Pure-Python brain (pip package, ZERO ROS2 imports)
│   └── langrobo_core/
│       ├── graph/     StateGraph topology, routing, handover resolution
│       ├── agents/    chat · local_agent(vision) · navigate · status · swiggy · tracker · supervisor
│       ├── tools/     look() · movement · reminders · household · episodic recall · handover
│       ├── services/  config · llm(+cloud fallback) · memory(Qdrant) · health API · logging · metrics
│       ├── utils/     history trimming · message projection · speech streaming · timing
│       └── bridges/   StubBridge (run everything without ROS2)
├── langrobo_ros/      ROS2 shim: agent_node + ROS2Bridge + studio_voice_node + launch + systemd
└── robot_interfaces/  Custom ROS2 interfaces
scripts/               run_brain.sh · run_microros.sh · dev.sh · dev_voice.sh · install_systemd.sh · latency_replay.py
graph_studio.py        LangGraph Studio entry point (langgraph dev)
```

The split is the architecture: **`langrobo_core` never imports rclpy** — the
whole brain runs and tests on any machine. `langrobo_ros` injects its bridge at
startup.

---

## Setup (Pi5, Ubuntu 24.04, ROS2 Jazzy)

```bash
# ROS2 Jazzy base (no desktop — saves ~400 MB RAM)
sudo apt install ros-jazzy-ros-base
sudo rosdep init && rosdep update            # skip init if already done

cd ~/ros2_ws
rosdep install --from-paths src --ignore-src -r -y

# Python deps: installs langrobo_core editable + its pinned stack
pip3 install --break-system-packages -r requirements.txt

# micro-ROS agent — built once in ~/microros_ws (see OPERATIONS.md)

colcon build --symlink-install
source install/setup.bash

cp example.env .env    # then fill in keys as they become available
```

## Run

```bash
# Production (24/7, auto-restart, structured logs) — one-time install:
./scripts/install_systemd.sh
journalctl -u langrobo-brain -f -o cat       # follow JSON logs

# Foreground (all-in-one):
ros2 launch langrobo_ros brain_launch.py

# Dev — LangGraph Studio UI + micro-ROS (don't run alongside systemd units):
./scripts/dev.sh

# Dev + voice — the same, plus STT/TTS: talk to the graph while stepping it:
./scripts/dev_voice.sh
```

Health check (see OPERATIONS.md for the full API):

```bash
curl -s localhost:8090/health
curl -s -H "Authorization: Bearer $LANGROBO_API_TOKEN" localhost:8090/status | jq
```

## Test (no robot, no LLM server, no keys needed)

```bash
cd src/langrobo_core && python3 -m pytest tests/ -q
```
