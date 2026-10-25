#!/usr/bin/env bash
# The front sonars' node (scripts/sonar_node.py) -- exec'd by langrobo-sonar.service,
# or run by hand. Same ROS setup as run_microros.sh: domain 0, Fast DDS, multicast.
#   SONAR_DEV=/dev/serial/by-path/...  the sonar ESP32's USB socket (default: usb-0:1:1.0)
#   SONAR_LEFT_IS_S1=0                 if S2 (TRIG GPIO18) is the left one
set -eo pipefail
set +u
source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset ROS_DISCOVERY_SERVER || true
unset ROS_LOCALHOST_ONLY
exec python3 "$(dirname "$0")/sonar_node.py"
