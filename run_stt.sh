#!/usr/bin/env bash
# Launch the Pi5 STT node with live logs. Ctrl-C to stop. See PI5_VOICE.md.
# Temporary [diag] logging (audio-callback heartbeat + ALSA warnings) is on.
#   ./run_stt.sh                 # provider from config (default: local, English)
#   ./run_stt.sh sarvam          # Telugu speech -> English text
set -eo pipefail
cd "$(dirname "$0")"
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ARGS=(--ros-args --params-file src/pi5_voice_pkg/config/voice_params.yaml)
[ -n "$1" ] && ARGS+=(-p stt_provider:="$1")
exec ros2 run pi5_voice_pkg stt_node "${ARGS[@]}"
