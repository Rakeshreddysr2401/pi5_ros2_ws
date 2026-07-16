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

# XRCE Agent v3.0.1 standalone (built 2026-07-16, ~/microros_ws/xrce_agent_3):
# the ROS-wrapped agent (XRCE 2.4.3) stack-smashes on this ESP32 client
# session (upstream Micro-XRCE-DDS-Agent issue #395) and died in a crash
# loop. The standalone agent bridges topics identically; the wrapper only
# added ros2-graph cosmetics. Revert: swap the exec lines back.
#   exec ros2 run micro_ros_agent micro_ros_agent udp4 --port $UDP_PORT
export LD_LIBRARY_PATH="$HOME/.local/xrce-agent-3/lib:${LD_LIBRARY_PATH:-}"
exec "$HOME/.local/xrce-agent-3/bin/MicroXRCEAgent" udp4 -p "$UDP_PORT" -v4
