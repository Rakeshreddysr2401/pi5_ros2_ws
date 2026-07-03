#!/usr/bin/env bash
# LangRobo brain (agent_node) — exec'd by langrobo-brain.service or run
# standalone: ./scripts/run_brain.sh [provider]
#
# Under systemd the micro-ROS agent is its own unit (langrobo-microros), so
# this passes start_micro_ros:=false. For an all-in-one foreground run use:
#   ros2 launch langrobo_ros brain_launch.py
#
# Do NOT run alongside `langgraph dev` — both drive /cmd_vel.

set -eo pipefail
PROVIDER="${1:-llamacpp}"

set +u
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE=$HOME/ros2_ws/fastdds_unicast.xml
unset ROS_LOCALHOST_ONLY

# .env (API keys, LANGROBO_* service settings) is loaded by agent_node itself
# via LANGROBO_ENV_FILE (default ~/ros2_ws/.env) — no wholesale `source` here,
# so stale tracing keys can't leak into the environment.

exec ros2 launch langrobo_ros brain_launch.py \
    provider:="$PROVIDER" start_micro_ros:=false
