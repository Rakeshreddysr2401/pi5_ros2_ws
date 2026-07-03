#!/usr/bin/env bash
# Dev loop: micro-ROS agent (UDP) + LangGraph Studio together.
#
# Usage:
#   ./scripts/dev.sh          # UDP port 8888 (default)
#   ./scripts/dev.sh 9999     # custom UDP port
#
# Do NOT run alongside the langrobo-brain systemd unit — both drive /cmd_vel
# and both would bind micro-ROS UDP 8888. Stop it first:
#   sudo systemctl stop langrobo-brain langrobo-microros
#
# Ctrl+C stops both cleanly.

set -eo pipefail
UDP_PORT="${1:-8888}"

set +u
source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
source ~/ros2_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE=$HOME/ros2_ws/fastdds_unicast.xml
unset ROS_LOCALHOST_ONLY

echo "==> micro-ROS agent: UDP port $UDP_PORT"
echo "==> LangGraph Studio: http://127.0.0.1:2024"
echo "(Ctrl+C to stop both)"

ros2 run micro_ros_agent micro_ros_agent udp4 --port "$UDP_PORT" &
MICRO_ROS_PID=$!

cleanup() {
    echo ""
    echo "==> Stopping..."
    kill "$MICRO_ROS_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

cd ~/ros2_ws
langgraph dev
