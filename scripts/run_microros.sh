#!/usr/bin/env bash
# micro-ROS agent (Pi5 ↔ ESP32 WiFi bridge) — exec'd by langrobo-microros.service
# or run standalone: ./scripts/run_microros.sh [udp_port]

set -eo pipefail
UDP_PORT="${1:-8888}"

set +u
source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
# Join the DDS graph via the "meeting point" (Fast DDS Discovery Server on this
# Pi5) BY NAME — no hardcoded IPs, works on any network. See NETWORKING.md.
# The meeting point runs ON this Pi5, so local clients use loopback — the
# mDNS name resolves IPv6-first on WiFi-only boots and the server is UDPv4,
# which silently broke registration (2026-07-06). The NAME is only for the
# Jetson side (pinned to IPv4 there — see NETWORKING.md).
export ROS_DISCOVERY_SERVER="127.0.0.1:11811"
unset ROS_LOCALHOST_ONLY

exec ros2 run micro_ros_agent micro_ros_agent udp4 --port "$UDP_PORT"
