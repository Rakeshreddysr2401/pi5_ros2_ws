#!/usr/bin/env bash
# One-time ROOT setup: the Pi 5 moves to ROS 2 Lyrical, the same distro as the Jetson (2026-10-25).
# Why: Jazzy (Fast DDS 2.14) here vs Lyrical (Fast DDS 3.6) on the Jetson -- the Jetson
# camera writer (image_bridge) intermittently never matches Pi subscribers, so the brain
# gets 0 frames. Jazzy stays installed as the rollback; nothing is removed.
# Run:  sudo ~/ros2_ws/scripts/setup_lyrical_root.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run it with sudo"; exit 1; }

echo "[1/3] NVIDIA ROS Lyrical repo for Ubuntu 24.04 arm64 (the same one the Jetson image uses)"
k=/usr/share/keyrings/nvidia-isaac-ros.gpg
curl -fsSL https://isaac.download.nvidia.com/isaac-ros/repos.key | gpg --dearmor --yes -o "$k"
echo "deb [arch=arm64 signed-by=$k] https://isaac.download.nvidia.com/isaac-ros/ubuntu/main noble main" \
  > /etc/apt/sources.list.d/nvidia-ros-lyrical.list
apt-get update
apt-get install -y --no-install-recommends \
  ros-lyrical-ros-base ros-lyrical-nav2-msgs ros-lyrical-action-msgs ros-lyrical-cv-bridge \
  ros-lyrical-visualization-msgs ros-lyrical-robot-state-publisher ros-lyrical-tf2-ros \
  ros-lyrical-std-srvs ros-lyrical-micro-ros-msgs ros-lyrical-launch-ros ros-lyrical-rosidl-runtime-py

echo "[2/3] DDS socket buffers as on the Jetson (the Pi had 208 KB and ~1 M dropped UDP packets)"
printf "net.core.rmem_max=67108864\nnet.core.rmem_default=8388608\n" > /etc/sysctl.d/60-kino-dds.conf
sysctl --system > /dev/null

echo "[3/3] front sonar service (TODO 0b: until now the node was started by hand, and it is down)"
cp /home/rakhi24/ros2_ws/src/langrobo_ros/systemd/langrobo-sonar.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now langrobo-sonar

echo "done. /opt/ros: $(ls /opt/ros | tr '\n' ' ') -- tell Claude; the rest needs no sudo"
