# =============================================================================
# example.bashrc — pi5_ros2_ws (Raspberry Pi 5)
#
# Copy the sections you need into your actual ~/.bashrc, then run:
#   source ~/.bashrc
#
# Or source this file directly for a one-time session:
#   source ~/pi5_ros2_ws/example.bashrc
# =============================================================================


# ── ROS2 base setup ───────────────────────────────────────────────────────────
source /opt/ros/jazzy/setup.bash


# ── Workspace (update path if you cloned somewhere else) ─────────────────────
export ROBOT_WS=~/pi5_ros2_ws
source "$ROBOT_WS/install/setup.bash"


# ── ROS2 domain — must match Jetson ──────────────────────────────────────────
export ROS_DOMAIN_ID=0

# Optional: CycloneDDS for more reliable cross-machine discovery
# export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp


# ── LLM server (Mac Mini or wherever llama.cpp runs) ─────────────────────────
export LLM_BASE_URL="http://192.168.31.24:8080/v1"   # update to your Mac Mini IP


# ── API keys — uncomment whichever provider you use ──────────────────────────
# export OPENAI_API_KEY=sk-...
# export ANTHROPIC_API_KEY=sk-ant-...
# export GOOGLE_API_KEY=...


# ── Swiggy + web search (optional) ───────────────────────────────────────────
# export SWIGGY_FOOD_MCP_URL=https://mcp.swiggy.com/food
# export SWIGGY_ACCESS_TOKEN=
# export TAVILY_API_KEY=tvly-...


# =============================================================================
# BUILD ALIASES
# =============================================================================

# Build entire workspace
alias rb='cd $ROBOT_WS && colcon build --symlink-install && source install/setup.bash'

# Build a single package  (usage: rbp ai_agent)
alias rbp='_rbp(){ cd $ROBOT_WS && colcon build --symlink-install --packages-select "$1" && source install/setup.bash; }; _rbp'

# Build without tests (faster)
alias rbn='cd $ROBOT_WS && colcon build --symlink-install --cmake-args -DBUILD_TESTING=OFF && source install/setup.bash'

# Clean build — wipes build/ install/ log/ and rebuilds from scratch
alias rbclean='cd $ROBOT_WS && rm -rf build install log && colcon build --symlink-install && source install/setup.bash'

# Re-source workspace only (after a build done in another terminal)
alias rsrc='source $ROBOT_WS/install/setup.bash && echo "Workspace sourced"'


# =============================================================================
# LAUNCH ALIASES  —  full system
# =============================================================================

# Full system — brain (all 7 agents) + micro-ROS agent  [DEFAULT]
alias robot='ros2 launch robot_brain brain_launch.py base_url:=$LLM_BASE_URL'

# Brain only — no motor control (useful when testing without the robot)
alias robot-brain='ros2 launch ai_agent agent.launch.py base_url:=$LLM_BASE_URL'

# Full system with a different LLM provider
alias robot-openai='ros2 launch robot_brain brain_launch.py provider:=openai'
alias robot-anthropic='ros2 launch robot_brain brain_launch.py provider:=anthropic'
alias robot-gemini='ros2 launch robot_brain brain_launch.py provider:=gemini'
alias robot-ollama='ros2 launch robot_brain brain_launch.py provider:=ollama base_url:=http://localhost:11434'
alias robot-llamacpp='ros2 launch robot_brain brain_launch.py provider:=llamacpp base_url:=$LLM_BASE_URL'

# Custom LLM server IP on the fly  (usage: robot-ip 192.168.1.50)
robot-ip() {
    ros2 launch robot_brain brain_launch.py base_url:="http://$1:8080/v1"
}

# micro-ROS agent only  (ESP32 bridge without the brain)
alias launch-microros='ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888'


# =============================================================================
# DEBUG / MONITORING ALIASES
# =============================================================================

# List all active topics / nodes / services
alias rtopics='ros2 topic list'
alias rnodes='ros2 node list'
alias rservices='ros2 service list'

# Echo key topics  (Ctrl+C to stop)
alias recho-in='ros2 topic echo /voice/user_input'          # what user said
alias recho-out='ros2 topic echo /voice/robot_speech'       # what robot speaks
alias recho-query='ros2 topic echo /vision/query'           # VLM question sent to Jetson
alias recho-answer='ros2 topic echo /vision/query_result'   # Moondream answer from Jetson
alias recho-vel='ros2 topic echo /cmd_vel'                  # wheel velocity to ESP32
alias recho-thinking='ros2 topic echo /brain/thinking'      # true while LLM is running

# Topic publish rate
alias rhz-input='ros2 topic hz /voice/user_input'
alias rhz-camera='ros2 topic hz /camera/color/image_raw'

# Node details
alias rnode-agent='ros2 node info /agent_node'

# Check topic connections  (usage: rcheck /voice/user_input)
alias rcheck='ros2 topic info -v'

# Call robot status service
alias rstatus='ros2 service call /robot/get_status std_srvs/srv/Trigger'

# System resources on Pi5
alias rcpu='top -b -n1 | head -20'
alias rmem='free -h'
alias rtemp='vcgencmd measure_temp'       # Pi5 CPU temperature


# =============================================================================
# TEST / INJECT ALIASES  —  simulate hardware input
# =============================================================================

# Simulate user speech — injects text directly into /voice/user_input
# Usage: rspeak hello robot
rspeak() {
    ros2 topic pub --once /voice/user_input std_msgs/msg/String "{data: \"$*\"}"
}

# Trigger the order delivery poll  (tests the Swiggy delivery flow)
# Usage: rorder-arrived ORD123456
rorder-arrived() {
    ros2 topic pub --once /voice/user_input std_msgs/msg/String \
        "{data: \"[SYSTEM] Check if Swiggy order $1 has been delivered\"}"
}

# Send a movement command directly to the robot (bypasses brain)
# Usage: rmove F:30  /  rmove L:90  /  rmove S
rmove() {
    ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.0}}"
    echo "Use rspeak to send movement commands through the brain, or publish Twist directly to /cmd_vel"
}

# Test TTS — make Jetson speak a sentence
rsay() {
    ros2 topic pub --once /voice/robot_speech std_msgs/msg/String "{data: \"$*\"}"
}

# Ask Moondream a visual question directly
rask-vision() {
    ros2 topic pub --once /vision/query std_msgs/msg/String "{data: \"$*\"}"
}


# =============================================================================
# CONVENIENCE
# =============================================================================

# Go to workspace root
alias ws='cd $ROBOT_WS'

# Go to ai_agent package
alias wsa='cd $ROBOT_WS/src/ai_agent/ai_agent'

# Tail latest colcon build log
alias rlog='tail -f $ROBOT_WS/log/latest_build/events.log'

# Kill all ROS2 processes on this machine
alias rkillall='pkill -9 -f "ros2|agent_node|micro_ros_agent" && echo "All ROS2 processes killed"'

# Show active ROS2 environment variables
alias renv='env | grep -E "^ROS|^AMENT|^COLCON|^RMW|^LLM"'

# Restart ROS2 daemon (fixes "cannot connect to daemon" errors)
alias rdaemon='ros2 daemon stop && ros2 daemon start && echo "ROS2 daemon restarted"'

# Print cheat sheet of all aliases
alias rhelp='echo "
BUILD:
  rb                   — build entire workspace
  rbp <pkg>            — build one package  (e.g. rbp ai_agent)
  rbclean              — clean + full rebuild
  rsrc                 — re-source without rebuilding

LAUNCH:
  robot                — full system: brain + micro-ROS agent  [DEFAULT]
  robot-brain          — brain only (no micro-ROS agent)
  robot-llamacpp       — use local llama.cpp (default)
  robot-openai         — use OpenAI API
  robot-anthropic      — use Anthropic Claude
  robot-gemini         — use Google Gemini
  robot-ollama         — use local Ollama
  robot-ip <ip>        — use custom LLM server IP
  launch-microros      — micro-ROS agent only (ESP32 bridge without brain)

DEBUG:
  rtopics / rnodes / rservices
  recho-in/out/query/answer/vel/thinking
  rhz-input/camera             — topic publish rates
  rstatus                      — call /robot/get_status service
  rtemp / rcpu / rmem          — Pi5 hardware monitoring

TEST (no Jetson needed):
  rspeak <text>        — simulate user saying something
  rsay <text>          — test TTS output
  rask-vision <q>      — send vision query to Moondream
  rorder-arrived <id>  — trigger Swiggy delivery flow

MISC:
  ws / wsa             — cd to workspace / ai_agent package
  rkillall             — kill all ROS2 processes
  rdaemon              — restart ROS2 daemon
  renv                 — show ROS/LLM env vars
"
'
