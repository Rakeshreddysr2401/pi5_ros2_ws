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
ROBOT_BODY="${ROBOT_BODY:-rover}"

set +u
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
# MULTICAST, not the discovery server (changed 2026-07-16): the Jetson
# perception stack MUST run plain multicast — the D555 is a raw DDS
# participant on the wire that discovery-server clients cannot see, so the
# whole Jetson side leaves ROS_DISCOVERY_SERVER unset. A client here would
# put this node on a separate discovery plane, invisible to the Jetson
# (this exact split silently isolated the brain until 2026-07-16).
# Multicast across this WiFi AP is verified working (Jetson<->Pi5).
unset ROS_DISCOVERY_SERVER || true
unset ROS_LOCALHOST_ONLY

# .env (API keys, LANGROBO_* service settings) is loaded by agent_node itself
# via LANGROBO_ENV_FILE (default ~/ros2_ws/.env) — no wholesale `source` here,
# so stale tracing keys can't leak into the environment.

exec ros2 launch langrobo_ros brain_launch.py \
    provider:="$PROVIDER" start_micro_ros:=false robot_body:="$ROBOT_BODY"
