#!/usr/bin/env bash
# Install + enable the LangRobo systemd units (run once, needs sudo):
#   ./scripts/install_systemd.sh
# After this the robot brain survives crashes and reboots:
#   systemctl status langrobo-brain langrobo-microros
#   journalctl -u langrobo-brain -f -o cat        # structured JSON logs

set -euo pipefail
cd "$(dirname "$0")/.."

sudo cp src/langrobo_ros/systemd/langrobo-discovery.service /etc/systemd/system/
sudo cp src/langrobo_ros/systemd/langrobo-microros.service  /etc/systemd/system/
sudo cp src/langrobo_ros/systemd/langrobo-brain.service     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now langrobo-discovery langrobo-microros langrobo-brain
systemctl --no-pager status langrobo-discovery langrobo-microros langrobo-brain || true
