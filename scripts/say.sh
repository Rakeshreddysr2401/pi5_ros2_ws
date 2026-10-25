#!/usr/bin/env bash
# say.sh -- type one turn to the REAL robot's brain, as if spoken, and print what it says back.
#
#   ./scripts/say.sh "Kino, what do you see?" [--wait 240] [--quiet 25]
#   ./scripts/say.sh --listen 120          # only print what it says
#
# The same as stt_node publishing a transcript (domain 0, /voice/user_input), via
# twin_chat.py --real. Use it instead of `ros2 topic pub --once`, which sends
# before the brain has matched it and loses about half the turns (2026-10-11).
# It is the real robot: a movement sentence MOVES IT -- only with someone watching.
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
unset ROS_DISCOVERY_SERVER
export ROS_DOMAIN_ID=0
if [ "${1:-}" = "--listen" ]; then exec python3 "$HERE/twin_chat.py" --real --listen "${2:-120}"; fi
exec python3 "$HERE/twin_chat.py" --real "$@"
