# ros_env.sh -- how every script on this Pi 5 gets ROS: ROS 2 Lyrical (the Jetson's distro)
# + this workspace's overlay (install/). Sourced by scripts/*.sh; in a shell:
#   source ~/ros2_ws/scripts/ros_env.sh
# Lyrical since 2026-10-25 (Jazzy here vs Lyrical on the Jetson left the brain blind); Jazzy
# was removed the same night. Build with scripts/build_ws.sh.
KINO_WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KINO_WS
case $- in *u*) _kino_u=1 ;; *) _kino_u= ;; esac
set +u
source /opt/ros/lyrical/setup.bash
# KINO_ROS_UNDERLAY_ONLY=1 (scripts/build_ws.sh): ROS alone, not our own previous build
if [ "${KINO_ROS_UNDERLAY_ONLY:-0}" != 1 ] && [ -f "$KINO_WS/install/setup.bash" ]; then
  source "$KINO_WS/install/setup.bash"
fi
if [ -n "$_kino_u" ]; then set -u; fi
unset _kino_u
