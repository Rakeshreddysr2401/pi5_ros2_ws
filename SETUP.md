# Pi5 Robot Brain — Device Setup Guide

This guide details how to set up the robot brain reasoning node and micro-ROS agent on a new device (Raspberry Pi 5 or Ubuntu-based controller) running **Ubuntu 24.04** and **ROS 2 Jazzy**.

---

## Prerequisites

Ensure ROS 2 Jazzy is installed. For headless robot setups, the base variant is recommended to save memory:
```bash
sudo apt update
sudo apt install -y ros-jazzy-ros-base
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

---

## Step-by-Step Setup

### Step 1: Install System & Build Dependencies
Install the package manager tools, VCS repository manager, and compilation libraries required for micro-ROS agent building:
```bash
sudo apt update
sudo apt install -y \
  python3-rosdep \
  python3-vcstool \
  flex \
  bison \
  libncurses-dev \
  libcurl4-openssl-dev
```

Initialize and update `rosdep` (if not already done):
```bash
sudo rosdep init
rosdep update
```

### Step 2: Install Python Packages
Since Ubuntu 24.04 uses an externally managed environment (PEP 668), install the pinned packages globally using the `--break-system-packages` flag:
```bash
cd ~/ros2_ws
pip3 install -r requirements.txt --break-system-packages
```

This also installs `langgraph-cli` (the `langgraph dev` command used for LangGraph Studio).

### Step 3: Install Main Workspace Dependencies
Use `rosdep` to download all other standard ROS 2 package dependencies declared in the packages:
```bash
cd ~/ros2_ws
rosdep install --from-paths src --ignore-src -r -y
```

### Step 4: Build the micro-ROS Agent Workspace
The micro-ROS agent is compiled in a dedicated workspace to keep main application builds fast.
> [!IMPORTANT]
> You **must** checkout the `jazzy` branch of `micro_ros_setup` to avoid version mismatches and package name errors (like `fastddsConfig.cmake` missing).

```bash
# 1. Create workspace and clone setup tool
mkdir -p ~/microros_ws/src
cd ~/microros_ws
git clone https://github.com/micro-ROS/micro_ros_setup.git src/micro_ros_setup

# 2. Checkout Jazzy branch and compile the setup tool
cd ~/microros_ws/src/micro_ros_setup
git checkout jazzy
cd ~/microros_ws
source /opt/ros/jazzy/setup.bash
colcon build

# 3. Create the agent sources and compile them
source install/setup.bash
ros2 run micro_ros_setup create_agent_ws.sh
ros2 run micro_ros_setup build_agent.sh
```

### Step 5: Configure Shell Sourcing (`~/.bashrc`)
Configure your shell to source the micro-ROS agent workspace and the main application workspace at startup. 

Add the following to the end of your `~/.bashrc` file:
```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=30                   # Replace with your domain ID if different
source ~/microros_ws/install/setup.bash   # Sources the micro-ROS agent command
source ~/ros2_ws/install/setup.bash       # Sources the robot brain nodes
```
Source it in your current shell:
```bash
source ~/.bashrc
```

### Step 6: Build the Application Workspace
Finally, build the packages inside `ros2_ws`:
```bash
cd ~/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

---

---

## LangGraph Studio Setup

LangGraph Studio gives you a browser UI to chat with the robot, inspect the agent graph, and step through routing decisions in real time.

The ESP32 connects to the Pi5 over **WiFi UDP** (not USB serial). The micro-ROS agent must be running before the ESP32 can receive `/cmd_vel` commands.

### One-time setup

```bash
cp example.env .env
# Edit .env — set your API key (e.g. OPENAI_API_KEY=sk-...)
# Optionally change STUDIO_PROVIDER / STUDIO_MODEL
```

Make `dev.sh` executable (do this once):

```bash
chmod +x ~/ros2_ws/dev.sh
```

### Starting Studio on Pi5

Use `dev.sh` — it starts the micro-ROS agent (background) and LangGraph Studio (foreground) together:

```bash
cd ~/ros2_ws
./dev.sh
```

Then:
1. Power on the ESP32 — it auto-connects over WiFi
2. Open [LangGraph Studio](https://smith.langchain.com/studio) in your browser
3. Connect to `http://<pi5-ip>:2024`

You should see `/rover_esp32` appear in `ros2 node list` once the ESP32 connects.

**What `dev.sh` does:**
- Starts `ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888` in the background
- Starts `langgraph dev` in the foreground
- Ctrl+C stops both cleanly

> **Important:** Do not run `langgraph dev` and `ros2 launch` at the same time — both would publish to the same ROS2 topics (`/voice/robot_speech`, `/cmd_vel`, etc.) and commands would interleave unpredictably.

If ROS2 is not sourced (e.g. on a dev laptop), Studio still works — chat and Swiggy tools function normally; movement/vision tools return stub messages instead of controlling the robot.

### Verifying the connection

In a second terminal after `./dev.sh`:

```bash
# Should show /rover_esp32 and /studio_bridge once ESP32 connects
ros2 node list

# Should show /cmd_vel in the list
ros2 topic list | grep cmd_vel
```

---

## Verifying Setup

### 1. Check ROS 2 packages are recognized:
```bash
ros2 pkg list | grep -E "ai_agent|robot_brain"
```

### 2. Verify the micro-ROS agent is executable:
```bash
ros2 run micro_ros_agent micro_ros_agent --help
```

---

## Laptop Setup (Dev Machine — No ROS2)

Run the full LangGraph graph and LangGraph Studio on your laptop without any robot hardware.
Chat, supervisor routing, and Swiggy tools work normally. Movement and vision tools return
stub messages (ROS2 not running) instead of controlling the robot.

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager

```bash
# Install uv (once, system-wide)
curl -LsSf https://astral.sh/uv/install.sh | sh
# or on Windows:
# powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Step 1: Clone the repo

```bash
git clone <repo-url>
cd pi5_ros2_ws
```

### Step 2: Create environment and install dependencies

```bash
# Create a venv and install all dependencies from requirements.txt
uv venv
uv pip install -r requirements.txt

# Make the local `ai_agent` package importable (it is NOT in requirements.txt).
# --no-deps keeps the pinned versions from requirements.txt untouched.
uv pip install -e ./src/ai_agent --no-deps

# Activate the venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows
```

> `uv` resolves and installs all packages in seconds. No `--break-system-packages`
> needed — the venv is fully isolated (no ROS2 on laptop, so no system packages to inherit).
>
> **Why the editable install?** `langgraph.json` references `graph_studio.py`, which
> imports `ai_agent`. On the Pi5, sourcing the ROS2 workspace puts `ai_agent` on the
> path; on a laptop there is no ROS2, so `langgraph dev` would fail with
> `ModuleNotFoundError: No module named 'ai_agent'` unless the package is installed
> editable as above. Do this once — after that a plain `langgraph dev` just works.

> **Keep versions pinned.** If `langgraph dev` ever crashes with
> `ImportError: cannot import name 'Graph' from 'langgraph.graph'`, the venv has drifted
> to langgraph 1.x while the CLI stayed old. Restore the pinned set with
> `uv pip install -r requirements.txt` (langgraph 0.3.34 / langgraph-cli 0.1.89).

### Step 3: Configure environment

```bash
cp example.env .env
# Edit .env and set your API key:
#   OPENAI_API_KEY=sk-...        (if using OpenAI)
#   ANTHROPIC_API_KEY=sk-ant-... (if using Anthropic)
#
# Optionally change the Studio LLM (use the Mac Mini Gemma for multimodal/look()):
#   STUDIO_PROVIDER=llamacpp
#   STUDIO_MODEL=gemma-4-E4B-it-Q8_0.gguf
#   STUDIO_BASE_URL=http://singireddys-mac-mini.local:8080/v1
#
# To test local_agent's look() vision without a camera, point this at any image:
#   STUDIO_TEST_IMAGE=/path/to/photo.jpg
```

### Step 4: Run LangGraph Studio

```bash
# Make sure the venv is active, then:
langgraph dev
```

Open [LangGraph Studio](https://smith.langchain.com/studio) and connect to `http://localhost:2024`.

### Adding / updating dependencies (laptop)

```bash
# Add a new package
uv pip install <package>
uv pip freeze > requirements.txt   # keep requirements.txt in sync

# Or edit requirements.txt directly, then:
uv pip install -r requirements.txt
```

### What works vs. what doesn't on laptop

| Feature | Works | Notes |
|---------|-------|-------|
| Chat / general questions | yes | full LLM routing |
| Supervisor agent routing | yes | full graph visible in Studio |
| Swiggy food ordering | yes | MCP tools connect to Swiggy server |
| `speak()` | no | logs text instead of publishing |
| `move_robot()` / `navigate_to_pose()` | no | returns error — no ROS2 |
| `navigate_to_visible_object()` | no | stub `get_target_result()` returns None → scans briefly then exits |
| `look()` (local_agent / vision) | partial | no live camera; serves `STUDIO_TEST_IMAGE` if set, else "no frame" |
| `get_robot_status()` | no | `call_service()` raises TimeoutError |
