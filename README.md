# LangRobo — Pi5 Robot Brain

The **brain** of a distributed home assistant robot ("Rakhi"). The Pi5 runs all
reasoning (a LangGraph supervisor + three agents) and bridges motor commands to
the chassis; the Jetson handles perception.

**Three agents, one KV cache slot each.** `chat` answers, `local_agent` sees,
`navigate` moves — and a `supervisor` routes between them. Every agent is one
`AgentSpec` in `registry.py`; the graph, the routing grammar and the llama.cpp
slot map all derive from it.

```
Mac Mini  ──────  llama.cpp — Gemma multimodal GGUF, --parallel 4 (one KV slot per agent)
Jetson    ──────  D555 depth cam · cuVSLAM · nvblox · Nav2 · VLM pixel→goal bridge
Pi 5      ──────  THIS REPO — LangGraph brain + STT/TTS + micro-ROS agent
ESP32     ──────  4-wheel drive chassis, 50 Hz PID (micro-ROS over WiFi UDP 8888)
```

**Docs:** [HOW_IT_WORKS.md](HOW_IT_WORKS.md) — end-to-end walkthrough (start here) ·
**[ARCHITECTURE_LLD.md](ARCHITECTURE_LLD.md) — the low-level design: every file, the turn lifecycle, where the latency goes** ·
[INTEGRATION_GAPS.md](INTEGRATION_GAPS.md) — what this brain asks of the rover that the rover does not answer ·
[OPERATIONS.md](OPERATIONS.md) — deploy, systemd, health API, troubleshooting ·
[TELEGRAM.md](TELEGRAM.md) — chat with the robot from your phone: setup + usage

---

## Repo layout

```
src/
├── langrobo_core/     Pure-Python brain (pip package, ZERO ROS2 imports)
│   └── langrobo_core/
│       ├── registry.py  ONE AgentSpec per agent — start here
│       ├── prompts.py   every system prompt, in one file
│       ├── fastpath.py  spoken movement command → wheels, no LLM
│       ├── graph/     StateGraph topology, entry routing, handover resolution
│       ├── agents/    factory (builds every responder from its spec) + supervisor
│       ├── tools/     look() · movement · approach · telegram · web · handover
│       ├── services/  config · llm(+cloud fallback) · telegram · permissions · health · logging · metrics
│       ├── utils/     history trimming · message projection · speech streaming · timing
│       └── bridges/   StubBridge (run everything without ROS2)
├── langrobo_ros/      ROS2 shim: agent_node + ROS2Bridge + launch + systemd
└── pi5_voice_pkg/     CPU-only STT + TTS on the Pi 5 itself
scripts/               run_brain.sh · run_microros.sh · dev.sh · install_systemd.sh · latency_replay.py
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

# Dev — LangGraph Studio UI + micro-ROS (don't run alongside systemd units).
# Studio draws the graph, which is the fastest way to SEE the topology:
./scripts/dev.sh

```

The llama.cpp server needs **`--parallel 4`** — one KV-cache slot per agent.
With fewer, agents share a slot and evict each other's cached prompt prefix;
agent_node warns at boot when that happens. See ARCHITECTURE_LLD.md §4.1.

Health check (see OPERATIONS.md for the full API):

```bash
curl -s localhost:8090/health
curl -s -H "Authorization: Bearer $LANGROBO_API_TOKEN" localhost:8090/status | jq
```

## Test (no robot, no LLM server, no keys needed)

```bash
cd src/langrobo_core && python3 -m pytest tests/ -q
```
