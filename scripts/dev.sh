#!/usr/bin/env bash
# Dev loop: LangGraph Studio on :2024, plus a micro-ROS agent if none is up.
#
# Usage:
#   ./scripts/dev.sh              # ESP32 on USB serial (default)
#   ./scripts/dev.sh udp [port]   # ESP32 on WiFi (run_microros.sh's arguments)
#
# Studio itself is scripts/start_studio.sh — the ONE place that sets its
# discovery (plain SUBNET, like agent_node and the Jetson) and loads the
# calibration in ~/.langrobo/brain.env. This used to set its own
# ROS_DISCOVERY_SERVER, which put Studio on a separate DDS graph from the
# rest of the robot: it started cleanly and saw no robot at all.
#
# Studio and langrobo-brain run the SAME graph and both can drive; only
# agent_node hears the Nav2 arrival report. To drive from Studio, stop the
# brain first:  sudo systemctl stop langrobo-brain
#
# Ctrl+C stops both.

set -eo pipefail

MICRO_ROS_PID=""
if systemctl is-active --quiet langrobo-microros; then
    echo "==> micro-ROS agent: already running (langrobo-microros) — not starting a second"
else
    echo "==> micro-ROS agent: ${1:-serial}"
    "$(dirname "$0")/run_microros.sh" "$@" &
    MICRO_ROS_PID=$!
fi

cleanup() {
    echo ""
    echo "==> Stopping..."
    [ -n "$MICRO_ROS_PID" ] && kill "$MICRO_ROS_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

echo "==> LangGraph Studio: http://127.0.0.1:2024"
echo "    from the laptop: ssh -N -L 2024:localhost:2024 rakhi24@rakhi24-desktop.local"
echo "    then open https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024"
"$(dirname "$0")/start_studio.sh" &
wait $!
cleanup
