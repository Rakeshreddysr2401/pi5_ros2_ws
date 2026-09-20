#!/usr/bin/env bash
# The Pi5 voice trio — audio_device_node (owns the speaker + mic), tts_node,
# stt_node. Exec'd by the langrobo-voice USER unit, or run by hand:
#   ./scripts/run_voice.sh
#
# A user unit, not a system one: PipeWire/WirePlumber live in the user
# session and wpctl/pactl only work there. See PI5_VOICE.md § Running it.
#
# Never run this alongside the Jetson's ai_stack voice role — both would
# transcribe and speak on the same /voice/* topics.

set -eo pipefail
cd "$(dirname "$0")/.."

set +u
source /opt/ros/jazzy/setup.bash
source install/setup.bash
set -u

export PYTHONUNBUFFERED=1
# Match the brain's DDS config EXACTLY (scripts/run_brain.sh): multicast on
# domain 0, Fast DDS, no discovery server — or the voice nodes end up on a
# graph the brain cannot see.
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset ROS_DISCOVERY_SERVER || true
unset ROS_LOCALHOST_ONLY

exec ros2 launch pi5_voice_pkg voice_launch.py
