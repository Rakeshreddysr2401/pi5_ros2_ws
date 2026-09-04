#!/usr/bin/env bash
# Launch the Pi5 TTS node (local Kokoro by default). Speaks anything published
# to /voice/robot_speech. Pair with ./tts_say.py to type text. Ctrl-C to stop.
# Override provider: ./run_tts.sh sarvam   (default: config value, i.e. local)
set -eo pipefail
cd "$(dirname "$0")"
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ARGS=(--ros-args --params-file src/pi5_voice_pkg/config/voice_params.yaml)
[ -n "$1" ] && ARGS+=(-p tts_provider:="$1")
exec ros2 run pi5_voice_pkg tts_node "${ARGS[@]}"
