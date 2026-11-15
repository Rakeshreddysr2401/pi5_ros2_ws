#!/usr/bin/env bash
# LangRobo brain (agent_node) — exec'd by langrobo-brain.service or run
# standalone: ./scripts/run_brain.sh [provider]
#
# Under systemd the micro-ROS agent is its own unit (langrobo-microros), so
# this passes start_micro_ros:=false. For an all-in-one foreground run use:
#   ros2 launch langrobo_ros brain_launch.py
#
# Do NOT run alongside `langgraph dev` — both drive /cmd_vel.

set -eo pipefail
PROVIDER="${1:-llamacpp}"

set +u
source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
set -u

# Python block-buffers stdout when it is a pipe, which systemd's journal
# is. Startup logs appeared (big first flush) and then NOTHING for 17
# minutes across a full conversation and a failed navigation — the
# rover was undiagnosable while it was actually driving. Line-buffer it.
export PYTHONUNBUFFERED=1

kino_dds          # domain + multicast discovery: ros_env.sh

# .env (API keys, LANGROBO_* service settings) is loaded by agent_node itself
# via LANGROBO_ENV_FILE (default ~/ros2_ws/.env) — no wholesale `source` here,
# so stale tracing keys can't leak into the environment.

exec ros2 launch langrobo_ros brain_launch.py \
    provider:="$PROVIDER" start_micro_ros:=false
