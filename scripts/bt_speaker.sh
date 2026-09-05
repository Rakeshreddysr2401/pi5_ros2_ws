#!/usr/bin/env bash
# Bluetooth speaker for the Pi5 voice pair (e.g. a Boat Stone).
#
#   ./scripts/bt_speaker.sh scan              # 20s discovery — find the MAC
#   ./scripts/bt_speaker.sh pair AA:BB:..     # pair + trust + connect (once)
#   ./scripts/bt_speaker.sh connect [MAC]     # connect + make it the default sink
#   ./scripts/bt_speaker.sh disconnect [MAC]
#   ./scripts/bt_speaker.sh profile a2dp|hfp [MAC]
#   ./scripts/bt_speaker.sh status
#
# MAC defaults to LANGROBO_BT_MAC, else bt_mac in voice_params.yaml, so the
# speaker is configured in exactly one place.
#
# Pairing is a ONE-TIME human step: put the speaker in pairing mode first
# (Boat Stone: hold the multifunction button until the LED blinks fast).
# After `pair`, the device is trusted and reconnects on its own.

set -uo pipefail
PARAMS="$HOME/ros2_ws/src/pi5_voice_pkg/config/voice_params.yaml"

mac_from_config() {
    [ -n "${LANGROBO_BT_MAC:-}" ] && { echo "$LANGROBO_BT_MAC"; return; }
    grep -oE "bt_mac:[[:space:]]*['\"]?([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}" "$PARAMS" 2>/dev/null \
        | grep -oE "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}" | head -1
}

need_daemon() {
    if ! systemctl is-active --quiet bluetooth; then
        echo "bluetooth.service is not running. Enable it once:"
        echo "    sudo systemctl enable --now bluetooth"
        exit 1
    fi
}

# BlueZ's DBus policy (/usr/share/dbus-1/system.d/bluetooth.conf) grants pairing
# to root and to the `bluetooth` group only. Pairing as an unprivileged user
# outside that group fails with an unhelpful org.freedesktop.DBus.Error.
# Connecting and audio routing afterwards need no privileges — only the initial
# pair/trust writes to /var/lib/bluetooth.
need_pair_rights() {
    [ "$(id -u)" = "0" ] && return 0
    id -nG | grep -qw bluetooth && return 0
    echo "Pairing needs privileges: you are not root and not in the 'bluetooth' group."
    echo "Either run this once with sudo:"
    echo "    sudo $0 pair ${1:-<MAC>}"
    echo "or join the group (needs a fresh login to take effect):"
    echo "    sudo usermod -aG bluetooth $USER"
    return 1
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
      MAC="${1:-$(mac_from_config)}"
      [ -z "$MAC" ] && { echo "usage: $0 pair AA:BB:CC:DD:EE:FF"; exit 2; }
      need_pair_rights "$MAC" || exit 1
      bluetoothctl --timeout 15 scan on >/dev/null 2>&1
      bluetoothctl pair "$MAC"
      bluetoothctl trust "$MAC"      # trust = reconnect without asking again
      bluetoothctl connect "$MAC"
      default_sink_for "$MAC"
      echo
      echo "Now set it in $PARAMS:"
      echo "    bt_mac: \"$MAC\""
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
