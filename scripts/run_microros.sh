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
export FASTRTPS_DEFAULT_PROFILES_FILE=$HOME/ros2_ws/fastdds_unicast.xml
unset ROS_LOCALHOST_ONLY

exec ros2 run micro_ros_agent micro_ros_agent udp4 --port "$UDP_PORT"
