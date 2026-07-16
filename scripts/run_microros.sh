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
# MULTICAST, not the discovery server (changed 2026-07-16): the Jetson
# stack runs plain multicast (the D555 is a raw DDS participant that
# discovery-server clients cannot see), so the ESP32 bridge must join the
# same multicast plane or /cmd_vel from Nav2/the brain never reaches it.
# Multicast across this WiFi AP is verified working (Jetson<->Pi5).
unset ROS_DISCOVERY_SERVER || true
unset ROS_LOCALHOST_ONLY

exec ros2 run micro_ros_agent micro_ros_agent udp4 --port "$UDP_PORT"
