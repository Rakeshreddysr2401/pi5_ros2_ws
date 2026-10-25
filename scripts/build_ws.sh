#!/usr/bin/env bash
# build_ws.sh -- build this workspace for the distro scripts/ros_env.sh picks
# (Lyrical by default since 2026-10-25), into its own build_<distro>/ install_<distro>/.
#   ./scripts/build_ws.sh [colcon args...]     e.g. --packages-select langrobo_ros
set -eo pipefail
cd "$(dirname "$0")/.."
KINO_ROS_UNDERLAY_ONLY=1
source scripts/ros_env.sh
if [ "$KINO_ROS_DISTRO" = jazzy ]; then B=build I=install; else B="build_$KINO_ROS_DISTRO" I="install_$KINO_ROS_DISTRO"; fi
echo "[build_ws] ROS $KINO_ROS_DISTRO -> $B/ $I/"
exec colcon build --symlink-install --build-base "$B" --install-base "$I" "$@"
