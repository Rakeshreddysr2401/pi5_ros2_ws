#!/usr/bin/env bash
# Dev mode, whole loop, one command: micro-ROS + LangGraph Studio + STT/TTS +
# the voice bridge. Talk to the graph and watch it step at the same time.
#
# Usage:
#   ./scripts/dev_voice.sh              # everything, UDP 8888
#   ./scripts/dev_voice.sh 9999         # custom micro-ROS UDP port
#   ./scripts/dev_voice.sh --no-voice   # just micro-ROS + langgraph dev (= dev.sh)
#   ./scripts/dev_voice.sh --no-micro   # no ESP32 link (no wheels needed)
#
# Reuses a `langgraph dev` already listening on :2024 instead of fighting it.
# Ctrl+C stops everything this script started.
#
# Never runs alongside langrobo-brain — it owns /cmd_vel AND /voice/user_input,
# so both would answer the same utterance. The preflight below refuses to start.

set -eo pipefail

UDP_PORT=8888
WANT_VOICE=1
WANT_MICRO=1
STUDIO_URL="http://127.0.0.1:2024"

for arg in "$@"; do
    case "$arg" in
        --no-voice) WANT_VOICE=0 ;;
        --no-micro) WANT_MICRO=0 ;;
        -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
        [0-9]*)     UDP_PORT="$arg" ;;
        *)          echo "unknown argument: $arg (try --help)"; exit 2 ;;
    esac
done

# ── Preflight ───────────────────────────────────────────────────────────────
if systemctl is-active --quiet langrobo-brain 2>/dev/null; then
    echo "REFUSING: langrobo-brain is running."
    echo "It owns /cmd_vel and subscribes /voice/user_input — two brains would"
    echo "answer every utterance. Stop it first:"
    echo "    sudo systemctl stop langrobo-brain langrobo-microros"
    exit 1
fi

LOG_DIR="${LANGROBO_DEV_LOGS:-/tmp/langrobo-dev}"
mkdir -p "$LOG_DIR"

set +u
source /opt/ros/jazzy/setup.bash
[ -f ~/microros_ws/install/setup.bash ] && source ~/microros_ws/install/setup.bash
source ~/ros2_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
# The meeting point runs on this Pi5, so local clients use loopback — the mDNS
# name resolves IPv6-first on WiFi-only boots and the server is UDPv4 (see
# NETWORKING.md / dev.sh).
export ROS_DISCOVERY_SERVER="127.0.0.1:11811"
unset ROS_LOCALHOST_ONLY

PIDS=()
cleanup() {
    echo ""
    echo "==> Stopping what this script started..."
    # setsid put each child in its own process group, so kill the GROUP:
    # `langgraph dev` spawns a server child that holds :2024 and survives a
    # plain kill of the parent, leaving the port taken after Ctrl+C.
    for pid in "${PIDS[@]}"; do
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    done
    exit 0
}
trap cleanup INT TERM

# ── micro-ROS (ESP32 wheels) ────────────────────────────────────────────────
if [ "$WANT_MICRO" = 1 ]; then
    # A second agent on the same port dies on bind, so reuse a live one — the
    # systemd unit or an earlier dev.sh commonly already holds it.
    if pgrep -f "micro_ros_agent udp4 --port $UDP_PORT" >/dev/null 2>&1; then
        echo "==> micro-ROS agent  : UDP $UDP_PORT (already running — reusing it)"
    else
        echo "==> micro-ROS agent  : UDP $UDP_PORT   (log: $LOG_DIR/microros.log)"
        setsid ros2 run micro_ros_agent micro_ros_agent udp4 --port "$UDP_PORT" \
            > "$LOG_DIR/microros.log" 2>&1 &
        PIDS+=($!)
    fi
fi

# ── LangGraph Studio ────────────────────────────────────────────────────────
if curl -sf -m 2 "$STUDIO_URL/ok" >/dev/null 2>&1; then
    echo "==> LangGraph Studio : $STUDIO_URL (already running — reusing it)"
else
    echo "==> LangGraph Studio : $STUDIO_URL   (log: $LOG_DIR/langgraph.log)"
    setsid bash -c 'cd ~/ros2_ws && exec langgraph dev --no-browser' \
        > "$LOG_DIR/langgraph.log" 2>&1 &
    PIDS+=($!)

    printf "    waiting for the graph server"
    for _ in $(seq 1 60); do
        if curl -sf -m 2 "$STUDIO_URL/ok" >/dev/null 2>&1; then break; fi
        printf "."
        sleep 1
    done
    echo ""
    if ! curl -sf -m 2 "$STUDIO_URL/ok" >/dev/null 2>&1; then
        echo "    server never came up — see $LOG_DIR/langgraph.log"
        cleanup
    fi
fi

# ── Voice (STT + TTS + the bridge) ──────────────────────────────────────────
if [ "$WANT_VOICE" = 0 ]; then
    if [ ${#PIDS[@]} -eq 0 ]; then
        # `wait` with no children returns immediately, so this used to exit at
        # once while looking like it was supervising something.
        echo "==> nothing new to start — micro-ROS and the graph server were"
        echo "    already running. Leaving them alone."
        exit 0
    fi
    echo "==> voice disabled (--no-voice). Ctrl+C to stop."
    wait
    exit 0
fi

echo "==> voice            : pi5 STT + TTS + studio_voice_node (foreground)"
echo "    speak the wake alias (rakhi / chotu / hey pi), or type at $STUDIO_URL"
echo "    — both are spoken, and both appear in Studio."
echo ""
ros2 launch langrobo_ros studio_voice_launch.py
