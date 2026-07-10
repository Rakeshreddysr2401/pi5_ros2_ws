#!/usr/bin/env bash
# LangRobo fleet launcher — run on the Pi5. One command to bring up the robot
# in either body:
#
#   ./scripts/fleet.sh sim      # SIMULATION body: laptop Gazebo sim (+ nav2)
#                               #   + Jetson voice AND isaac_ros (perception)
#   ./scripts/fleet.sh rover    # REAL body: Jetson ai_stack voice pipeline
#                               #   (+ this Pi5's micro-ROS agent for the ESP32
#                               #   wheels). No isaac_ros — the rover has no
#                               #   depth camera / lidar yet.
#   ./scripts/fleet.sh stop     # park the robot: stop the body, keep brain up
#   ./scripts/fleet.sh down     # full shutdown incl. this Pi5's services (sudo)
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

# mDNS names by default; override when mDNS flakes (it does, transiently):
#   LANGROBO_LAPTOP_HOST=192.168.1.12 ./scripts/fleet.sh sim
LAPTOP_HOST="${LANGROBO_LAPTOP_HOST:-rakhi24.local}"
JETSON_HOST="${LANGROBO_JETSON_HOST:-rakhi-jetson.local}"
LAPTOP=rakhi24@$LAPTOP_HOST
JETSON=rakhi24@$JETSON_HOST
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"

SIM_SCRIPT=/workspace/ros2_ws/src/rover_sim/rover_bringup/scripts/fleet_sim.sh
ROLE_SCRIPT="~/robot/scripts/fleet_role.sh"
BRAIN_ENV=/home/rakhi24/.langrobo/brain.env

# Which body (rover|sim) the brain's cmd_vel should drive — see ros2_bridge.py
# and CLAUDE.md "Simulation laptop". Only restarts the brain when the body is
# actually changing, so re-running `fleet.sh rover` while already in rover
# mode doesn't interrupt a live conversation.
set_body() {
    local body="$1" current=""
    [ -f "$BRAIN_ENV" ] && current=$(sed -n 's/^ROBOT_BODY=//p' "$BRAIN_ENV" | head -1)
    if [ "$current" = "$body" ]; then
        return 0
    fi
    mkdir -p "$(dirname "$BRAIN_ENV")"
    echo "ROBOT_BODY=$body" > "$BRAIN_ENV"
    if sudo -n systemctl restart langrobo-brain 2>/dev/null \
        || systemctl restart langrobo-brain 2>/dev/null; then
        echo "pi5:    robot_body switched to '$body' (brain restarted)"
    else
        echo "pi5:    robot_body set to '$body' in $BRAIN_ENV, but the brain"
        echo "        restart needs sudo — run: sudo systemctl restart langrobo-brain"
    fi
}

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
    set_body sim
    ensure_local_units
    if reachable "$LAPTOP_HOST"; then
        echo "laptop: starting sim..."
        $SSH $LAPTOP "$SIM_SCRIPT start" || echo "laptop: sim start FAILED"
    else
        echo "laptop: UNREACHABLE — is it powered on and on the wifi? (sim not started)"
    fi
    if reachable "$JETSON_HOST"; then
        # Sim mode needs BOTH Jetson roles: voice (real mic/speaker — you
        # still talk to the robot while the body is simulated) AND perception
        # (isaac_ros consuming the sim's /cam_1 depth/RGB topics).
        echo "jetson: starting voice + perception..."
        $SSH $JETSON "$ROLE_SCRIPT voice start" || echo "jetson: voice start FAILED"
        $SSH $JETSON "$ROLE_SCRIPT perception start" || echo "jetson: perception start FAILED"
    else
        echo "jetson: UNREACHABLE (voice/perception not started)"
    fi
    echo "fleet: SIM mode up. Nav2 needs ~1 min in the house world; check: $0 status"
    ;;
rover)
    set_body rover
    ensure_local_units
    sudo -n systemctl start langrobo-microros 2>/dev/null \
        || systemctl start langrobo-microros 2>/dev/null || true
    echo "pi5:    microros=$(systemctl is-active langrobo-microros)"
    if reachable "$JETSON_HOST"; then
        echo "jetson: starting voice pipeline..."
        $SSH $JETSON "$ROLE_SCRIPT voice start" || echo "jetson: voice start FAILED"
    else
        echo "jetson: UNREACHABLE (voice not started)"
    fi
    echo "fleet: ROVER mode up (isaac_ros not needed — rover has no depth cam/lidar yet)."
    ;;
stop)
    # Park the robot: stop the body (sim + Jetson roles). No password needed.
    # Brain + discovery stay up so chat/Telegram keeps listening.
    if reachable "$LAPTOP_HOST"; then
        $SSH $LAPTOP "$SIM_SCRIPT stop" || true
    fi
    if reachable "$JETSON_HOST"; then
        $SSH $JETSON "$ROLE_SCRIPT voice stop" || true
        $SSH $JETSON "$ROLE_SCRIPT perception stop" || true
    fi
    echo "fleet: robot body stopped. Brain + discovery still up (Telegram/chat alive)."
    echo "       Full shutdown incl. Pi5 services: $0 down"
    ;;
down)
    # Full shutdown: everything 'stop' does, PLUS this Pi5's own services
    # (brain, micro-ROS wheels, discovery meeting point). Those are systemd
    # system units, so this asks for your password once.
    if reachable "$LAPTOP_HOST"; then
        $SSH $LAPTOP "$SIM_SCRIPT stop" || true
    fi
    if reachable "$JETSON_HOST"; then
        $SSH $JETSON "$ROLE_SCRIPT voice stop" || true
        $SSH $JETSON "$ROLE_SCRIPT perception stop" || true
    fi
    echo "pi5:    stopping brain, micro-ROS, discovery (needs sudo)..."
    sudo systemctl stop langrobo-brain langrobo-microros langrobo-discovery
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)"
    echo "fleet: fully DOWN. Bring back with: $0 sim   (or rover)"
    echo "       (these units are enabled, so they'll also restart on next Pi5 boot)"
    ;;
status)
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)"
    if reachable "$LAPTOP_HOST"; then
        $SSH $LAPTOP "$SIM_SCRIPT status" 2>/dev/null || true
    else
        echo "laptop: unreachable"
    fi
    if reachable "$JETSON_HOST"; then
        $SSH $JETSON "$ROLE_SCRIPT voice status" 2>/dev/null || true
        $SSH $JETSON "$ROLE_SCRIPT perception status" 2>/dev/null || true
    else
        echo "jetson: unreachable"
    fi
    ;;
*)
    echo "usage: $0 {sim|rover|stop|down|status}"
    echo "  sim    start simulation body (laptop Gazebo+Nav2 + Jetson voice+isaac_ros)"
    echo "  rover  start real body (Pi5 micro-ROS wheels + Jetson voice)"
    echo "  stop   stop the robot body; keep brain + discovery (Telegram) alive"
    echo "  down   full shutdown incl. Pi5 brain/discovery/microros (asks sudo)"
    echo "  status show what's up everywhere"
    exit 2
    ;;
esac
