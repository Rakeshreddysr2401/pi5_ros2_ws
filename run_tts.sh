#!/usr/bin/env bash
# Launch the Pi5 TTS node (local Kokoro by default). Speaks anything published
# to /voice/robot_speech. Pair with ./tts_say.py to type text. Ctrl-C to stop.
# Override provider: ./run_tts.sh sarvam   (default: config value, i.e. local)
set -eo pipefail
cd "$(dirname "$0")"
source /opt/ros/jazzy/setup.bash
source install/setup.bash
# Match the brain's DDS config EXACTLY (scripts/run_brain.sh): default multicast
# discovery on domain 0, Fast DDS. The brain deliberately *unsets*
# ROS_DISCOVERY_SERVER — setting it here puts us on a separate graph and we'd
# never hear /voice/robot_speech.
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset ROS_DISCOVERY_SERVER || true
ARGS=(--ros-args --params-file src/pi5_voice_pkg/config/voice_params.yaml)
[ -n "$1" ] && ARGS+=(-p tts_provider:="$1")
exec ros2 run pi5_voice_pkg tts_node "${ARGS[@]}"
