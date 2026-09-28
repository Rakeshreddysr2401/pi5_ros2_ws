# LangRobo — Pi5 Robot Brain

The **brain** of a distributed home assistant robot ("Mitra"). The Pi5 runs all
reasoning (a LangGraph state machine over three agents) and bridges motor commands to
the chassis; the Jetson handles perception.

**Three agents, one KV cache slot each, and no router above them.** `chat`
answers and routes, `local_agent` sees, `navigate` moves. Every agent is one
`AgentSpec` in `registry.py`; the graph, the routing grammar and the llama.cpp
slot map all derive from it.

```
Mac Mini  ──────  llama.cpp — Gemma multimodal GGUF, --parallel 4 (a KV slot per agent + the photo survey)
Jetson    ──────  ~/rover: D555 + RPLidar · fused pose · slam · nvblox · Nav2 + exact moves · VLM pixel→goal
Laptop    ──────  RViz (pushed and started by the Jetson's `./rover view`) · Gazebo sim body
Pi 5      ──────  THIS REPO — LangGraph brain + STT/TTS + micro-ROS agent
ESP32     ──────  4-wheel drive chassis, 50 Hz PID (micro-ROS over WiFi UDP 8888)
```

**Docs:** [HOW_IT_WORKS.md](HOW_IT_WORKS.md) — end-to-end walkthrough (start here) ·
**[ARCHITECTURE_LLD.md](ARCHITECTURE_LLD.md) — the low-level design: every file, the turn lifecycle, where the latency goes** ·
[INTEGRATION_GAPS.md](INTEGRATION_GAPS.md) — what this brain asks of the rover that the rover does not answer ·
[OPERATIONS.md](OPERATIONS.md) — deploy, systemd, health API, troubleshooting ·
[TELEGRAM.md](TELEGRAM.md) — chat with the robot from your phone: setup + usage ·
[PI5_VOICE.md](PI5_VOICE.md) — the Pi5 voice trio (speaker/mic owner, STT, TTS) ·
[VOICE_ROADMAP.md](VOICE_ROADMAP.md) — the phased voice plan and what is done ·
[WAKE_WORD_INTEGRATION.md](WAKE_WORD_INTEGRATION.md) — train and plug in the "Mitra" wake word

---

## Repo layout

```
src/
├── langrobo_core/     Pure-Python brain (pip package, ZERO ROS2 imports)
│   └── langrobo_core/
│       ├── registry.py  ONE AgentSpec per agent — start here
│       ├── prompts.py   every system prompt, in one file
│       ├── graph/     StateGraph topology, entry routing, handover resolution
│       ├── agents/    factory — every agent is built from its spec, no exceptions
│       ├── tools/     look() · movement · approach · telegram · web · handover
│       ├── services/  config · llm(+cloud fallback) · telegram · permissions · health · logging · metrics
│       ├── utils/     history trimming · message projection · speech streaming · timing
│       └── bridges/   StubBridge (run everything without ROS2)
├── langrobo_ros/      ROS2 shim: agent_node + ROS2Bridge + launch + systemd
└── pi5_voice_pkg/     CPU-only STT + TTS on the Pi 5 itself
scripts/               fleet.sh (whole robot) · run_*.sh (systemd entry points) · start_studio.sh · dev.sh
                       latency_replay.py · llm_cache_check.py (KV-slot PASS/FAIL) · topic_rates.py
                       wake_*.py · train_mitra_local.py / train_rakhi_local.py (wake-word trainers, laptop)
                       mic_check.sh (is the mic hearing you?) · bt_*.{sh,py} · tts_say.py
notebooks/             train_mitra_wakeword.ipynb (the supported wake-word trainer — WAKE_WORD_INTEGRATION.md)
data/test_pos|test_neg wake-word spot-check clips
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

**The whole robot, from this Pi 5** (after a power cycle, or any time):

```bash
./scripts/fleet.sh rover     # bring up: micro-ROS + the Jetson's `./rover up` (skipped if already up)
./scripts/fleet.sh check     # prove every link with data — read-only, ~20 s, OK/FAIL + what to run
./scripts/fleet.sh status    # what is running where
./scripts/fleet.sh stop      # park the body (Jetson off); brain + voice + Telegram stay up
./scripts/fleet.sh down      # full shutdown before power-off (sudo)
```

Claude Code skills wrap these with the troubleshooting steps: `/robot-start`,
`/robot-stop`, `/bt-audio`. Then talk to it — say **"Mitra"** in the sentence —
or message it on Telegram ([TELEGRAM.md](TELEGRAM.md)). How the machines find
each other: [NETWORKING.md](NETWORKING.md). The Jetson side (`~/rover` there)
has its own README.md and STARTUP.md.

**This Pi 5's own services:**

```bash
# Production (24/7, auto-restart, structured logs) — one-time install:
./scripts/install_systemd.sh
journalctl -u langrobo-brain -f -o cat       # follow JSON logs

# Foreground (all-in-one):
ros2 launch langrobo_ros brain_launch.py

# LangGraph Studio (draws the graph; runs the SAME graph and can drive):
./scripts/start_studio.sh    # beside the running brain
./scripts/dev.sh             # same, plus a micro-ROS agent if none is running
# from the laptop: ssh -N -L 2024:localhost:2024 rakhi24@rakhi24-desktop.local
#   then https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
# Every real turn is also traced to LangSmith, project `pi5` (key in .env).

```

The llama.cpp server needs **`--parallel 4`** — one KV-cache slot per agent, plus one for the photo survey (and `--swa-full`: `scripts/llm_cache_check.py`).
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
