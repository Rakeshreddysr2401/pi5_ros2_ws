#!/usr/bin/env bash
# Start micro-ROS agent (UDP) + LangGraph Studio together.
#
# Usage:
#   ./dev.sh          # UDP port 8888 (default)
#   ./dev.sh 9999     # custom UDP port
#
# Ctrl+C stops both cleanly.

set -euo pipefail

UDP_PORT="${1:-8888}"

source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
source ~/ros2_ws/install/setup.bash

echo "==> micro-ROS agent: UDP port $UDP_PORT"
echo "==> LangGraph Studio: http://127.0.0.1:2024"
echo "(Ctrl+C to stop both)"
echo ""

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
