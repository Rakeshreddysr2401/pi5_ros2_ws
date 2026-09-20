#!/usr/bin/env bash
# Bluetooth speaker for the Pi5 voice pair (e.g. a Boat Stone).
#
#   ./scripts/bt_speaker.sh pair              # GUIDED: add a new speaker/headphones
#   ./scripts/bt_speaker.sh scan              # 20s discovery — find a MAC by hand
#   ./scripts/bt_speaker.sh pair AA:BB:..     # pair + trust + connect a known MAC
#   ./scripts/bt_speaker.sh connect [MAC]     # connect + make it the default sink
#   ./scripts/bt_speaker.sh disconnect [MAC]
#   ./scripts/bt_speaker.sh profile a2dp|hfp [MAC]
#   ./scripts/bt_speaker.sh status
#
# MAC defaults to LANGROBO_BT_MAC, else the first entry of bt_devices in
# voice_params.yaml.
#
# Pairing is a ONE-TIME human step: put the speaker in pairing mode first
# (Boat Stone: hold the multifunction button until the LED blinks fast).
# After `pair`, the device is trusted and audio_device_node (see
# PI5_VOICE.md) connects it, routes it and reconnects it on its own from
# then on — `connect`/`profile` below are only for poking at it by hand.

set -uo pipefail
PARAMS="$HOME/ros2_ws/src/pi5_voice_pkg/config/voice_params.yaml"

mac_from_config() {
    [ -n "${LANGROBO_BT_MAC:-}" ] && { echo "$LANGROBO_BT_MAC"; return; }
    grep -A1 -E "bt_devices:" "$PARAMS" 2>/dev/null \
        | grep -oE "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}" | head -1
}

need_daemon() {
    if ! systemctl is-active --quiet bluetooth; then
        echo "bluetooth.service is not running. Enable it once:"
        echo "    sudo systemctl enable --now bluetooth"
        exit 1
    fi
}

default_sink_for() {   # $1 = MAC -> make its PipeWire node the default sink
    local suffix="${1//:/_}"
    local id
    id=$(wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' \
         | grep -i "${suffix^^}" | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
    [ -z "$id" ] && id=$(wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' \
         | grep -iE 'stone|bluez' | grep -oE '[0-9]+\.' | head -1 | tr -d '.')
    if [ -n "$id" ]; then
        wpctl set-default "$id" && echo "default sink -> node $id"
    else
        echo "no PipeWire sink for $1 yet (give it a second, or check 'wpctl status')"
    fi
}

CMD="${1:-status}"; shift || true

case "$CMD" in
  scan)
      need_daemon
      echo "scanning 20s — put the speaker in pairing mode now..."
      bluetoothctl --timeout 20 scan on >/dev/null 2>&1
      bluetoothctl devices | sed 's/^/  /'
      ;;
  pair)
      need_daemon
      # No address = the guided flow: scan, pick the new device by name, pair.
      [ -z "${1:-}" ] && exec python3 "$(dirname "$0")/bt_pair.py"
      MAC="$1"
      bluetoothctl --timeout 15 scan on >/dev/null 2>&1
      if ! bluetoothctl pair "$MAC"; then
          echo "pairing failed. Is the device in pairing mode and in range?"
          echo "If bluez answered with a DBus/NotAuthorized error, this user may lack pairing rights:"
          echo "    sudo usermod -aG bluetooth $USER   (then log in again)   — or run this once with sudo"
          exit 1
      fi
      bluetoothctl trust "$MAC"      # trust = reconnect without asking again
      bluetoothctl connect "$MAC"
      default_sink_for "$MAC"
      echo
      echo "audio_device_node will pick it up on its own. To PREFER it over"
      echo "other paired devices, add it first in $PARAMS:"
      echo "    bt_devices: [\"$MAC\"]"
      ;;
  connect)
      need_daemon
      MAC="${1:-$(mac_from_config)}"
      [ -z "$MAC" ] && { echo "no MAC given and none in voice_params.yaml"; exit 2; }
      for i in 1 2 3; do
          bluetoothctl connect "$MAC" 2>&1 | tail -1
          if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
              default_sink_for "$MAC"; exit 0
          fi
          sleep 3
      done
      echo "could not connect $MAC — is the speaker powered on and in range?"
      exit 1
      ;;
  disconnect)
      MAC="${1:-$(mac_from_config)}"
      bluetoothctl disconnect "$MAC" 2>&1 | tail -1
      ;;
  profile)
      PROF="${1:-a2dp}"; MAC="${2:-$(mac_from_config)}"
      case "$PROF" in
        a2dp) TARGET=a2dp-sink ;;
        hfp)  TARGET=headset-head-unit ;;
        *)    echo "profile must be a2dp or hfp"; exit 2 ;;
      esac
      if ! command -v pactl >/dev/null; then
          echo "switching profiles needs pactl:  sudo apt install pulseaudio-utils"
          echo "(a2dp works without it — WirePlumber selects it on connect)"
          exit 1
      fi
      pactl set-card-profile "bluez_card.${MAC//:/_}" "$TARGET" && echo "profile -> $TARGET"
      ;;
  status)
      echo "bluetooth.service : $(systemctl is-active bluetooth 2>/dev/null)"
      MAC="$(mac_from_config)"
      echo "configured MAC    : ${MAC:-<none>}"
      if [ -n "$MAC" ] && systemctl is-active --quiet bluetooth; then
          bluetoothctl info "$MAC" 2>/dev/null | grep -E "Name|Connected|Trusted" | sed 's/^/  /'
      fi
      echo "PipeWire sinks:"
      wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | sed 's/^/  /'
      ;;
  *)  sed -n '2,18p' "$0"; exit 2 ;;
esac
