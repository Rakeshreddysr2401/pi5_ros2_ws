#!/usr/bin/env bash
# Launch the Pi5 TTS node (local Kokoro by default). Speaks anything published
# to /voice/robot_speech. Pair with ./scripts/tts_say.py to type text.
# Override provider: ./scripts/run_tts.sh sarvam  (default: the config value)
set -eo pipefail
cd "$(dirname "$0")/.."   # repo root: src/ and install/ live there
source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
kino_dds          # the brain's DDS setup exactly (ros_env.sh), or we are on a graph it cannot see
ARGS=(--ros-args --params-file src/pi5_voice_pkg/config/voice_params.yaml)
[ -n "$1" ] && ARGS+=(-p tts_provider:="$1")
exec ros2 run pi5_voice_pkg tts_node "${ARGS[@]}"
