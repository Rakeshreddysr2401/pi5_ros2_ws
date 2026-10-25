#!/usr/bin/env bash
# build_ws.sh -- build this workspace against ROS 2 Lyrical into build/ install/.
#   ./scripts/build_ws.sh [colcon args...]     e.g. --packages-select langrobo_ros
# Always from the repo root: a colcon build started inside src/ leaves stray
# build/ install/ log/ folders there (found in src/langrobo_core, 2026-10-25).
set -eo pipefail
cd "$(dirname "$0")/.."
KINO_ROS_UNDERLAY_ONLY=1
source scripts/ros_env.sh
exec colcon build --symlink-install "$@"
