#!/bin/bash
# start_studio.sh — LangGraph Studio for the rover brain. See rover/OPERATIONS.md §7.
#
# Run from anywhere; it cd's to the repo root itself, because the langgraph CLI
# reads langgraph.json and .env from the WORKING DIRECTORY, not from its own path.
#
# NOTE: no `set -u`. ROS's setup.bash references unset variables, so `set -u`
# aborts this script the moment it is sourced -- silently, if you have sent
# stderr to /dev/null. That cost a debugging round; leave it off.

cd "$HOME/ros2_ws" || { echo "no ~/ros2_ws"; exit 1; }

# ROS must match agent_node's own discovery settings. The Pi 5 uses plain SUBNET
# discovery; a stale ROS_DISCOVERY_SERVER gives a bridge that starts perfectly
# cleanly and silently sees no robot at all.
source /opt/ros/jazzy/setup.bash >/dev/null
source install/setup.bash        >/dev/null
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE

# The robot CALIBRATION lives in ~/.langrobo/brain.env, which systemd loads for
# langrobo-brain.service via EnvironmentFile=. Studio is not started by systemd,
# so without this it runs the SAME GRAPH with uncalibrated constants -- and the
# failure is silent and entirely plausible: movement.py falls back to
# _STEADY_STATE_ANGULAR_VEL = the COMMANDED 5.0 rad/s, which the body never
# reaches, so every timed turn runs ~4.2x short. Ask for 360 and get ~86.
#
# Found 2026-09-10: the rotation fix measured correctly through agent_node
# (360 -> 357 deg) and appeared completely unfixed when tested from Studio,
# because these are two processes and only one of them was ever given the file.
# Anything else added to brain.env has the same trap. See rover/TODO.md 36.
if [ -f "$HOME/.langrobo/brain.env" ]; then
    set -a
    . "$HOME/.langrobo/brain.env"
    set +a
    echo "loaded calibration from ~/.langrobo/brain.env"
else
    echo "WARNING: no ~/.langrobo/brain.env - Studio will use UNCALIBRATED defaults"
fi

# --allow-blocking: the graph does synchronous ROS and HTTP work, and without it
# the server raises on the first blocking call.
echo "starting: cwd=$(pwd) domain=$ROS_DOMAIN_ID"
exec "$HOME/.local/bin/langgraph" dev \
    --no-browser --allow-blocking --host 127.0.0.1 --port 2024
