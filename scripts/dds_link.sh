# dds_link.sh -- kino_link: put this process's Fast DDS on the Jetson <-> Pi 5 cable when it is up.
# No ROS here, so the standalone micro-ROS agent (run_microros.sh) can use it as well as
# kino_dds (ros_env.sh). Sourced; defines one function.
#
# Real robot (ROS_DOMAIN_ID 0) + cable (eth0 = 10.0.0.2, NM profile kino-link): Fast DDS only on
# the cable, see fastdds_cable.xml and docs/NETWORKING.md. Twin (other domains) and KINO_LINK=wifi
# keep plain WiFi. Just after boot (or with a cable plugged in) the address can be a few seconds
# behind: 2026-11-17 langrobo-voice started 2 s before eth0 and came up on WiFi. Waits up to 20 s.
kino_link() {
  unset FASTDDS_DEFAULT_PROFILES_FILE
  [ "${ROS_DOMAIN_ID:-0}" = 0 ] && [ "${KINO_LINK:-cable}" != wifi ] || return 0
  local i
  for i in $(seq 1 20); do
    ip -4 -br addr show eth0 2>/dev/null | grep -q " 10\.0\.0\.2/" && break
    [ "$(cut -d. -f1 /proc/uptime)" -lt 120 ] || [ "$(cat /sys/class/net/eth0/carrier 2>/dev/null)" = 1 ] || break
    sleep 1
  done
  if ip -4 -br addr show eth0 2>/dev/null | grep -q " 10\.0\.0\.2/"; then
    export FASTDDS_DEFAULT_PROFILES_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/fastdds_cable.xml"
  fi
  return 0
}
