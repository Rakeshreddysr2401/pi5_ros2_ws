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
# Find the Jetson via the "meeting point" (Fast DDS Discovery Server on this
# Pi5) BY NAME — no hardcoded IPs, works on any network. See NETWORKING.md.
# The meeting point runs ON this Pi5, so local clients use loopback — the
# mDNS name resolves IPv6-first on WiFi-only boots and the server is UDPv4,
# which silently broke registration (2026-07-06). The NAME is only for the
# Jetson side (pinned to IPv4 there — see NETWORKING.md).
export ROS_DISCOVERY_SERVER="127.0.0.1:11811"
unset ROS_LOCALHOST_ONLY

# .env (API keys, LANGROBO_* service settings) is loaded by agent_node itself
# via LANGROBO_ENV_FILE (default ~/ros2_ws/.env) — no wholesale `source` here,
# so stale tracing keys can't leak into the environment.

exec ros2 launch langrobo_ros brain_launch.py \
    provider:="$PROVIDER" start_micro_ros:=false
