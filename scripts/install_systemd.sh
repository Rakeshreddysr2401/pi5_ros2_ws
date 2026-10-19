#!/usr/bin/env bash
# Install + enable the LangRobo systemd units (run once, needs sudo):
#   ./scripts/install_systemd.sh
# After this the robot brain survives crashes and reboots:
#   systemctl status langrobo-brain langrobo-microros
#   journalctl -u langrobo-brain -f -o cat        # structured JSON logs

set -euo pipefail
cd "$(dirname "$0")/.."

sudo cp src/langrobo_ros/systemd/langrobo-microros.service  /etc/systemd/system/
sudo cp src/langrobo_ros/systemd/langrobo-brain.service     /etc/systemd/system/
sudo cp src/langrobo_ros/systemd/langrobo-sonar.service     /etc/systemd/system/
# Retire the Fast DDS discovery server (langrobo-discovery): the fleet is plain
# multicast since 2026-07-16 (NETWORKING.md) and nothing used it.
if [ -e /etc/systemd/system/langrobo-discovery.service ]; then
    sudo systemctl disable --now langrobo-discovery || true
    sudo rm -f /etc/systemd/system/langrobo-discovery.service
fi
sudo systemctl daemon-reload
sudo systemctl enable --now langrobo-microros langrobo-brain langrobo-sonar
systemctl --no-pager status langrobo-microros langrobo-brain langrobo-sonar || true

# Voice is a USER unit (PipeWire lives in the user session). Lingering makes
# the user session — and so this unit — start at boot without a login.
mkdir -p ~/.config/systemd/user
cp src/langrobo_ros/systemd/langrobo-voice.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now langrobo-voice
sudo loginctl enable-linger "$USER"
systemctl --user --no-pager status langrobo-voice || true
