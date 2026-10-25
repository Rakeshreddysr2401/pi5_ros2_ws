# ros_env.sh -- the ONE place that picks this Pi 5's ROS 2 distro and its workspace overlay.
# Sourced by every scripts/*.sh that needs ROS. Since 2026-10-25: Lyrical, the same distro as
# the Jetson (Jazzy here vs Lyrical there left the brain blind: the Jetson camera writer
# intermittently never matched Pi subscribers).
#
#   roll back to Jazzy:  echo jazzy > ~/.langrobo/ros_distro   (then restart the langrobo services)
#   one shell only:      KINO_ROS_DISTRO=jazzy ./scripts/...
#
# One workspace build per distro, so both stay usable: Jazzy = build/ install/ (as before),
# any other = build_<distro>/ install_<distro>/ -- build with scripts/build_ws.sh.
KINO_ROS_DISTRO="${KINO_ROS_DISTRO:-$(cat "$HOME/.langrobo/ros_distro" 2>/dev/null || echo lyrical)}"
KINO_WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$KINO_ROS_DISTRO" = jazzy ]; then KINO_WS_INSTALL="$KINO_WS/install"
else KINO_WS_INSTALL="$KINO_WS/install_$KINO_ROS_DISTRO"; fi
export KINO_ROS_DISTRO KINO_WS KINO_WS_INSTALL
case $- in *u*) _kino_u=1 ;; *) _kino_u= ;; esac
set +u
source "/opt/ros/$KINO_ROS_DISTRO/setup.bash"
# KINO_ROS_UNDERLAY_ONLY=1 (scripts/build_ws.sh): ROS alone, not our own previous build
if [ "${KINO_ROS_UNDERLAY_ONLY:-0}" != 1 ] && [ -f "$KINO_WS_INSTALL/setup.bash" ]; then
  source "$KINO_WS_INSTALL/setup.bash"
fi
if [ -n "$_kino_u" ]; then set -u; fi
unset _kino_u
