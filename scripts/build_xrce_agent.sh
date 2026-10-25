#!/usr/bin/env bash
# build_xrce_agent.sh -- (re)build the wheels' micro-ROS agent: eProsima Micro XRCE-DDS Agent
# v3.0.1, standalone, into ~/.local/xrce-agent-3 (what scripts/run_microros.sh runs).
#
# Why standalone (2026-07-16): the ROS-wrapped micro_ros_agent (XRCE 2.4.3) stack-smashes on
# this ESP32 client session (upstream Micro-XRCE-DDS-Agent issue #395). The standalone agent
# needs no ROS at all; its superbuild bundles its own Fast DDS (3.6.2 when last built = the
# Lyrical version), so the ESP32's topics speak the same DDS as the rest of the robot.
#
# Recipe recorded 2026-10-25 from the build that runs today (~/microros_ws/xrce_agent_3,
# CMakeCache: Release, defaults otherwise); that tree was then deleted. Not re-run since:
# the superbuild fetches Fast DDS's 3.x branch, so a rebuild may bring a newer 3.x --
# check `ls ~/.local/xrce-agent-3/lib | grep fastdds` and /wheel_state (20 Hz) after.
set -euo pipefail
PREFIX="$HOME/.local/xrce-agent-3"
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT
git clone --depth 1 --branch v3.0.1 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git "$SRC/agent"
cmake -S "$SRC/agent" -B "$SRC/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX"
cmake --build "$SRC/build" -j"$(nproc)"
cmake --install "$SRC/build"
echo "installed: $PREFIX/bin/MicroXRCEAgent -- restart: sudo systemctl restart langrobo-microros"
