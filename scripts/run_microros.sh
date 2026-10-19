#!/usr/bin/env bash
# micro-ROS agent (Pi5 ↔ ESP32 bridge) — exec'd by langrobo-microros.service
# or run standalone:
#   ./scripts/run_microros.sh                 # USB serial (default since 2026-10-07)
#   ./scripts/run_microros.sh serial [dev]    # same, another device
#   ./scripts/run_microros.sh udp [port]      # the old WiFi link, UDP 8888
#
# The transport must match the firmware's UROS_SERIAL switch
# (~/rover/phase1/firmware/rover_firmware_v2.ino on the Jetson): 1 = serial,
# 0 = WiFi. A mismatch is silent -- the agent runs, /wheel_state never appears.
# Serial needs rakhi24 in the dialout group (sudo usermod -aG dialout rakhi24).

set -eo pipefail
TRANSPORT="${1:-serial}"
# The wheels' ESP32 by the USB SOCKET it is in, not by-id (2026-10-19): the
# front-sonar ESP32 (a second board, same CP2102 chip) has the SAME by-id
# (...CP2102..._0001: a generic serial number), so with both plugged in by-id
# could open the sonar board and the wheels would never connect. by-path names
# the physical socket: keep the wheels' board in THIS one (it has been since
# 2026-10-12), the sonar board in any other. Moved: this path is missing and the
# service says so -- `ls -l /dev/serial/by-path/` shows the new one.
# ESP32_DEV=... in the environment overrides it.
ESP32_DEV="${ESP32_DEV:-/dev/serial/by-path/platform-xhci-hcd.0-usb-0:2:1.0-port0}"
SERIAL_DEV="${2:-$ESP32_DEV}"
UDP_PORT="${2:-8888}"

set +u
source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
set -u

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
# MULTICAST, not the discovery server (changed 2026-07-16): the Jetson
# stack runs plain multicast (the D555 is a raw DDS participant that
# discovery-server clients cannot see), so the ESP32 bridge must join the
# same multicast plane or /cmd_vel from Nav2/the brain never reaches it.
# Multicast across this WiFi AP is verified working (Jetson<->Pi5).
unset ROS_DISCOVERY_SERVER || true
unset ROS_LOCALHOST_ONLY

# XRCE Agent v3.0.1 standalone (built 2026-07-16, ~/microros_ws/xrce_agent_3):
# the ROS-wrapped agent (XRCE 2.4.3) stack-smashes on this ESP32 client
# session (upstream Micro-XRCE-DDS-Agent issue #395) and died in a crash
# loop. The standalone agent bridges topics identically; the wrapper only
# added ros2-graph cosmetics. Revert: swap the exec lines back.
#   exec ros2 run micro_ros_agent micro_ros_agent udp4 --port $UDP_PORT
export LD_LIBRARY_PATH="$HOME/.local/xrce-agent-3/lib:${LD_LIBRARY_PATH:-}"
AGENT="$HOME/.local/xrce-agent-3/bin/MicroXRCEAgent"
case "$TRANSPORT" in
    serial)
        [ -e "$SERIAL_DEV" ] || { echo "wheels' ESP32 not in its USB socket: $SERIAL_DEV missing" \
            "(moved to another socket? ls -l /dev/serial/by-path/)" >&2; exit 1; }
        # Reset the ESP32 first (RTS -> EN, DTR high keeps it out of the
        # bootloader). Without this a restarted agent never reconnects: when an
        # agent goes away mid-session the board sits re-sending heartbeats for
        # that dead session and never opens a new one (seen 2026-10-07; over
        # WiFi its ping timeout recovered it). Opening the port does NOT reset
        # this board. The wheels are stopped through the ~1 s reboot.
        python3 -c "import serial,time; s=serial.Serial(); s.port='$SERIAL_DEV'; s.dtr=False; s.rts=True; s.open(); time.sleep(0.1); s.rts=False; s.close()"
        exec "$AGENT" serial --dev "$SERIAL_DEV" -b 115200 -v4 ;;
    udp)
        exec "$AGENT" udp4 -p "$UDP_PORT" -v4 ;;
    *)
        echo "usage: $0 [serial [dev] | udp [port]]" >&2; exit 2 ;;
esac
