#!/usr/bin/env bash
# LangRobo DDS "meeting point" — Fast DDS Discovery Server (Pi5 <-> Jetson).
#
# Both machines' ROS2 nodes are CLIENTS of this server via
#   ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811
# It brokers discovery BY NAME, so there are no hardcoded IPs and it works
# unchanged on any network — direct Ethernet cable, home WiFi, or a friend's
# WiFi. Nothing here ever needs the internet. See NETWORKING.md.
#
# exec'd by langrobo-discovery.service, or run standalone:
#   ./scripts/run_discovery.sh [udp_port]

set -eo pipefail
DISCOVERY_PORT="${1:-11811}"

set +u
source /opt/ros/jazzy/setup.bash
set -u

# Server id 0 (first slot in ROS_DISCOVERY_SERVER), listening on all interfaces
# (default 0.0.0.0) so both the cable (192.168.2.x) and WiFi (192.168.1.x) paths
# can reach it. The server is domain-agnostic; clients apply ROS_DOMAIN_ID.
exec fastdds discovery -i 0 -p "$DISCOVERY_PORT"
