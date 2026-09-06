#!/usr/bin/env bash
# Launch the Pi5 STT node with live logs. Ctrl-C to stop. See PI5_VOICE.md.
# Temporary [diag] logging (audio-callback heartbeat + ALSA warnings) is on.
#   ./scripts/run_stt.sh         # provider from config (default: local, English)
#   ./scripts/run_stt.sh sarvam  # Telugu speech -> English text
set -eo pipefail
cd "$(dirname "$0")/.."   # repo root: src/ and install/ live there
source /opt/ros/jazzy/setup.bash
source install/setup.bash
# Match the brain's DDS config EXACTLY (scripts/run_brain.sh): default multicast
# discovery on domain 0, Fast DDS. The brain deliberately *unsets*
# ROS_DISCOVERY_SERVER — setting it here puts us on a separate graph the brain
# can't see (then STT publishes /voice/user_input into the void).
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset ROS_DISCOVERY_SERVER || true
ARGS=(--ros-args --params-file src/pi5_voice_pkg/config/voice_params.yaml)
[ -n "$1" ] && ARGS+=(-p stt_provider:="$1")
exec ros2 run pi5_voice_pkg stt_node "${ARGS[@]}"
