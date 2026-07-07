#!/usr/bin/env bash
# LangRobo fleet launcher — run on the Pi5. One command to bring up the robot
# in either body:
#
#   ./scripts/fleet.sh sim      # SIMULATION body: laptop Gazebo sim (+ nav2)
#                               #   + Jetson isaac_ros (perception) container
#   ./scripts/fleet.sh rover    # REAL body: Jetson ai_stack voice pipeline
#                               #   (+ this Pi5's micro-ROS agent for the ESP32
#                               #   wheels). No isaac_ros — the rover has no
#                               #   depth camera / lidar yet.
#   ./scripts/fleet.sh stop     # stop the remote pieces of BOTH modes
#   ./scripts/fleet.sh status   # who's up, everywhere
#
# The brain (langrobo-brain) and the DDS meeting point (langrobo-discovery)
# run here in both modes. Each machine can still be started by itself:
#   laptop:  /workspace/ros2_ws/src/rover_sim/rover_bringup/scripts/fleet_sim.sh
#   jetson:  ~/robot/scripts/fleet_role.sh {voice|perception} ...
#
# Machines are reached by mDNS name (NETWORKING.md) with passwordless ssh.

set -eo pipefail
CMD="${1:-status}"

LAPTOP=rakhi24@rakhi24.local
JETSON=rakhi24@rakhi-jetson.local
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"

SIM_SCRIPT=/workspace/ros2_ws/src/rover_sim/rover_bringup/scripts/fleet_sim.sh
ROLE_SCRIPT="~/robot/scripts/fleet_role.sh"

ensure_local_units() {
    sudo -n systemctl start langrobo-discovery 2>/dev/null \
        || systemctl start langrobo-discovery 2>/dev/null || true
    sudo -n systemctl start langrobo-brain 2>/dev/null \
        || systemctl start langrobo-brain 2>/dev/null || true
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)"
}

reachable() { timeout 4 ping -c1 -W2 "$1" >/dev/null 2>&1; }

case "$CMD" in
sim)
    ensure_local_units
    if reachable rakhi24.local; then
        echo "laptop: starting sim..."
        $SSH $LAPTOP "$SIM_SCRIPT start" || echo "laptop: sim start FAILED"
    else
        echo "laptop: UNREACHABLE — is it powered on and on the wifi? (sim not started)"
    fi
    if reachable rakhi-jetson.local; then
        echo "jetson: starting perception container..."
        $SSH $JETSON "$ROLE_SCRIPT perception start" || echo "jetson: perception start FAILED"
    else
        echo "jetson: UNREACHABLE (perception not started)"
    fi
    echo "fleet: SIM mode up. Nav2 needs ~1 min in the house world; check: $0 status"
    ;;
rover)
    ensure_local_units
    sudo -n systemctl start langrobo-microros 2>/dev/null \
        || systemctl start langrobo-microros 2>/dev/null || true
    echo "pi5:    microros=$(systemctl is-active langrobo-microros)"
    if reachable rakhi-jetson.local; then
        echo "jetson: starting voice pipeline..."
        $SSH $JETSON "$ROLE_SCRIPT voice start" || echo "jetson: voice start FAILED"
    else
        echo "jetson: UNREACHABLE (voice not started)"
    fi
    echo "fleet: ROVER mode up (isaac_ros not needed — rover has no depth cam/lidar yet)."
    ;;
stop)
    if reachable rakhi24.local; then
        $SSH $LAPTOP "$SIM_SCRIPT stop" || true
    fi
    if reachable rakhi-jetson.local; then
        $SSH $JETSON "$ROLE_SCRIPT voice stop" || true
        $SSH $JETSON "$ROLE_SCRIPT perception stop" || true
    fi
    echo "fleet: remote pieces stopped (brain/discovery/microros on this Pi5 left as-is;"
    echo "       use systemctl to stop those)"
    ;;
status)
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)"
    if reachable rakhi24.local; then
        $SSH $LAPTOP "$SIM_SCRIPT status" 2>/dev/null || true
    else
        echo "laptop: unreachable"
    fi
    if reachable rakhi-jetson.local; then
        $SSH $JETSON "$ROLE_SCRIPT voice status" 2>/dev/null || true
        $SSH $JETSON "$ROLE_SCRIPT perception status" 2>/dev/null || true
    else
        echo "jetson: unreachable"
    fi
    ;;
*)
    echo "usage: $0 {sim|rover|stop|status}"
    exit 2
    ;;
esac
