#!/usr/bin/env bash
# Launch the Pi5 STT node with live logs. Ctrl-C to stop. See docs/voice/PI5_VOICE.md.
# Temporary [diag] logging (audio-callback heartbeat + ALSA warnings) is on.
#   ./scripts/run_stt.sh         # provider from config (default: local, English)
#   ./scripts/run_stt.sh sarvam  # Telugu speech -> English text
set -eo pipefail
cd "$(dirname "$0")/.."   # repo root: src/ and install/ live there
source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
kino_dds          # the brain's DDS setup exactly (ros_env.sh), or we are on a graph it cannot see
ARGS=(--ros-args --params-file src/pi5_voice_pkg/config/voice_params.yaml)
[ -n "$1" ] && ARGS+=(-p stt_provider:="$1")
exec ros2 run pi5_voice_pkg stt_node "${ARGS[@]}"
