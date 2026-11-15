#!/usr/bin/env bash
# The Pi5 voice trio — audio_device_node (owns the speaker + mic), tts_node,
# stt_node. Exec'd by the langrobo-voice USER unit, or run by hand:
#   ./scripts/run_voice.sh
#
# A user unit, not a system one: PipeWire/WirePlumber live in the user
# session and wpctl/pactl only work there. See docs/voice/PI5_VOICE.md § Running it.
#
# Never run this alongside the Jetson's ai_stack voice role — both would
# transcribe and speak on the same /voice/* topics.

set -eo pipefail
cd "$(dirname "$0")/.."

set +u
source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
set -u

export PYTHONUNBUFFERED=1
kino_dds          # the brain's DDS setup exactly (ros_env.sh), or the voice nodes are on a graph it cannot see

exec ros2 launch pi5_voice_pkg voice_launch.py
