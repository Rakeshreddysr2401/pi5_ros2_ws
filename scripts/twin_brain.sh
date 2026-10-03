#!/usr/bin/env bash
# twin_brain.sh — a SECOND brain, for the Mitra twin. The real one is untouched.
#
#   ./scripts/twin_brain.sh up | down | status | logs [lines]
#   ./scripts/twin_brain.sh say "go near the chair"     # talk to it, print its replies
#   ./scripts/twin_brain.sh listen [seconds]            # just print what it says
#   ./scripts/twin_brain.sh manual on|off               # the twin's own teleop switch
#
# The twin (github mitra_sim) is a simulated home: the laptop runs the world
# (Gazebo), the Jetson runs the rover's own nav stack on it, both on ROS domain
# 42 (MITRA_DOMAIN). This runs the SAME agent_node, unchanged, on that domain:
#
#   - ROS_DOMAIN_ID=42: it hears only the twin; its /cmd_vel, /reach and
#     /goal_exec reach only the twin. Domain 0 -- the real robot, the real
#     brain (langrobo-brain), voice and teleop -- never sees it.
#   - LANGROBO_STATE_DIR=~/.langrobo_twin: its photos-of-simulated-rooms,
#     saved places and object memory never mix with the real robot's.
#   - Telegram OFF (your phone keeps talking to the real robot), no voice
#     (the mic and speaker belong to the real one); talk to it with `say`.
#   - its teleop switch is a file (manual on|off), never the real phone page.
#   - health API on :8092 (the real brain has :8090, teleop :8091).
#   - LangSmith traces (when on) go to the project "mitra-twin".
#
# It shares the Mac mini's llama.cpp slots with the real brain: while you use
# the twin, the real brain's caches go cold (its next reply is slower).
#
# Whole twin at once (laptop world + Jetson stack + this): fleet.sh twin.
set -u
DOMAIN="${MITRA_DOMAIN:-42}"
[ "$DOMAIN" = "0" ] && { echo "refusing: domain 0 is the REAL robot's" >&2; exit 1; }
STATE="${TWIN_STATE_DIR:-$HOME/.langrobo_twin}"
PIDF="$STATE/brain.pid"
LOG="$STATE/brain.log"
MODE="$STATE/teleop_mode.json"
HERE="$(cd "$(dirname "$0")" && pwd)"

rosenv() {
  set +u
  source /opt/ros/jazzy/setup.bash
  source "$HOME/ros2_ws/install/setup.bash"
  set -u
  export ROS_DOMAIN_ID="$DOMAIN" RMW_IMPLEMENTATION=rmw_fastrtps_cpp PYTHONUNBUFFERED=1
  unset ROS_DISCOVERY_SERVER ROS_LOCALHOST_ONLY FASTRTPS_DEFAULT_PROFILES_FILE
}

alive() { local p; p=$(cat "$PIDF" 2>/dev/null) && [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

case "${1:-status}" in
up)
  if alive; then echo "twin brain: already up (pid $(cat "$PIDF"), domain $DOMAIN)"; exit 0; fi
  mkdir -p "$STATE"
  [ -f "$MODE" ] || echo '{"manual": false}' > "$MODE"
  (
    rosenv
    export LANGROBO_STATE_DIR="$STATE"
    export LANGROBO_TELEGRAM_TOKEN=""          # set-but-empty: .env cannot turn it back on
    export LANGROBO_HEALTH_PORT=8092
    export LANGROBO_TELEOP_MODE_URL="file://$MODE"
    export LANGROBO_WARM_ALL=0                  # do not evict the real brain's caches at start
    export LANGSMITH_PROJECT=mitra-twin LANGCHAIN_PROJECT=mitra-twin
    setsid ros2 launch langrobo_ros brain_launch.py provider:=llamacpp \
        start_micro_ros:=false robot_body:=rover > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDF"
  )
  printf "twin brain: starting on domain %s" "$DOMAIN"
  for _ in $(seq 1 60); do
    grep -q "Startup complete" "$LOG" 2>/dev/null && { echo " -- up (log $LOG)"; exit 0; }
    alive || { echo " -- DIED:"; tail -20 "$LOG"; exit 1; }
    printf "."; sleep 1
  done
  echo " -- not ready after 60 s:"; tail -20 "$LOG"; exit 1
  ;;
down)
  if alive; then
    p=$(cat "$PIDF")
    kill -INT -- "-$p" 2>/dev/null; sleep 3; kill -KILL -- "-$p" 2>/dev/null
    echo "twin brain: down"
  else
    echo "twin brain: was not running"
  fi
  rm -f "$PIDF"
  ;;
status)
  if alive; then
    echo "twin brain: up (pid $(cat "$PIDF"), domain $DOMAIN, state $STATE, teleop $(cat "$MODE"))"
    curl -s -m 2 localhost:8092/health; echo
  else
    echo "twin brain: down"
  fi
  ;;
logs)
  tail -n "${2:-40}" "$LOG" ;;
say)
  shift
  [ $# -ge 1 ] || { echo "usage: $0 say \"what to say\" [--wait SECONDS]"; exit 2; }
  alive || { echo "twin brain is down: $0 up"; exit 1; }
  rosenv; exec python3 "$HERE/twin_chat.py" "$@" ;;
listen)
  rosenv; exec python3 "$HERE/twin_chat.py" --listen "${2:-120}" ;;
manual)
  case "${2:-}" in
    on)  echo '{"manual": true}' > "$MODE"; echo "twin teleop: MANUAL (the twin brain stops and refuses to drive)" ;;
    off) echo '{"manual": false}' > "$MODE"; echo "twin teleop: AUTO" ;;
    *)   echo "twin teleop: $(cat "$MODE" 2>/dev/null || echo 'no file yet')" ;;
  esac ;;
*)
  sed -n '2,8p' "$0" ;;
esac
