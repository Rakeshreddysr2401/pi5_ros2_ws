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

# --allow-blocking: the graph does synchronous ROS and HTTP work, and without it
# the server raises on the first blocking call.
echo "starting: cwd=$(pwd) domain=$ROS_DOMAIN_ID"
exec "$HOME/.local/bin/langgraph" dev \
    --no-browser --allow-blocking --host 127.0.0.1 --port 2024
