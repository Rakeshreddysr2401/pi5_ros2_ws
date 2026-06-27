#!/usr/bin/env bash
# Production launcher — voice-driven robot brain with LangSmith tracing.
#
# Usage:
#   ./prod.sh            # default: Mac Mini gemma (llamacpp)
#   ./prod.sh openai     # switch to OpenAI gpt-4o-mini (cloud)
#
# Starts micro-ROS agent (ESP32 bridge) + agent_node (LangGraph brain) via
# brain_launch.py. Voice loop: Jetson STT -> agent_node -> LLM -> Jetson TTS.
# Ctrl+C stops everything.
#
# Do NOT run alongside dev.sh — both bind micro-ROS UDP 8888 and both drive /cmd_vel.

set -eo pipefail

PROVIDER="${1:-llamacpp}"

# ROS workspaces
set +u
source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
source ~/ros2_ws/install/setup.bash
set -u

# ROS2 networking — Ethernet-only DDS (must match Jetson)
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE=$HOME/ros2_ws/fastdds_unicast.xml
unset ROS_LOCALHOST_ONLY

# Load .env -> LangSmith tracing (LANGCHAIN_TRACING_V2, LANGSMITH_API_KEY) + API keys
set -a
source ~/ros2_ws/.env
set +a

echo "ROS_DOMAIN_ID=$ROS_DOMAIN_ID"
echo "RMW_IMPLEMENTATION=$RMW_IMPLEMENTATION"
echo "FASTRTPS_DEFAULT_PROFILES_FILE=$FASTRTPS_DEFAULT_PROFILES_FILE"
echo "LLM provider=$PROVIDER"
echo "LangSmith tracing=${LANGCHAIN_TRACING_V2:-off} project=${LANGCHAIN_PROJECT:-default}"
echo ""
echo "==> Launching brain (micro-ROS agent + agent_node)"
echo "(Ctrl+C to stop)"
echo ""

exec ros2 launch robot_brain brain_launch.py provider:="$PROVIDER"
