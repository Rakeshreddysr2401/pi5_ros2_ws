# Networking — how the four machines find each other

Short version: **every ROS 2 process on every machine uses plain DDS
multicast on `ROS_DOMAIN_ID=0`, and every machine is reached by its mDNS
name.** No discovery server, no IP lists, no XML profiles.

## The machines

| machine | mDNS name | WiFi IP (DHCP unless noted) | ROS runs in | what it does |
|---|---|---|---|---|
| Pi 5 | `rakhi24-desktop.local` | 192.168.1.16 (fixed) | host (systemd) | brain, voice, micro-ROS agent, teleop :8091 |
| Jetson Orin | `rakhi-jetson.local` | 192.168.1.15 | `rover` container, host network | camera, lidar, pose, slam, nvblox, nav2, VLM bridge |
| Mac Mini | `singireddys-mac-mini.local` | moves | — (HTTP only) | llama.cpp LLM + VLM on :8080 |
| Laptop | `rakhi24.local` | moves | host | RViz (`./rover view` finds it) and the Gazebo sim |
| D555 camera | — | 192.168.11.55 (PoE) | itself — a raw DDS participant | depth + IR + IMU |
| ESP32 | — | WiFi | micro-ROS → Pi 5 UDP 8888 | wheels |

A direct cable (Pi 5 `eth0` 192.168.2.10 ↔ Jetson 192.168.2.20) exists but
has not reliably carried data; WiFi is the working path.

## The rules

1. **`ROS_DOMAIN_ID=0` and `ROS_DISCOVERY_SERVER` unset — everywhere.** The
   D555 is a raw DDS participant that cannot talk to a discovery server, so
   the whole fleet moved to multicast on 2026-07-16 (commit `279a466`).
   A process that still has `ROS_DISCOVERY_SERVER` set starts cleanly and
   **sees nothing** — the most expensive silent failure this robot has had.
   Every run script here (`run_brain.sh`, `run_microros.sh`, `run_voice.sh`,
   `start_studio.sh`, `dev.sh`) unsets it; the Jetson's `./rover` does too.
2. **Names, not IPs.** The brain calls the Mac Mini by name, `fleet.sh` sshes
   the Jetson and laptop by name. Only the Pi 5's IP is fixed (the Jetson's
   `./rover` uses it as `PI5_HOST`).
3. **The laptop and the Mac Mini move on DHCP.** Never diagnose them by IP.

`langrobo-discovery.service` (a Fast DDS discovery server on :11811) still
exists and still starts at boot. **Nothing uses it any more** — it is a
leftover of the pre-2026-07-16 scheme, harmless, and can be disabled with
`sudo systemctl disable --now langrobo-discovery` whenever convenient.

## Checking the link

Prove it with **data**, not node lists:

```bash
# On the Pi 5 (same env as the brain):
source /opt/ros/jazzy/setup.bash; unset ROS_DISCOVERY_SERVER; export ROS_DOMAIN_ID=0
ros2 topic hz /odom                      # ~20 Hz = the Jetson's fused pose reaches the Pi 5
ros2 topic hz /camera/color/image_raw/compressed   # only while the brain subscribes
curl -s localhost:8090/status | jq .runtime.camera_frame_age_s   # the brain's own view

getent ahostsv4 rakhi-jetson.local       # Jetson resolvable?
curl -s http://singireddys-mac-mini.local:8080/health   # LLM up?
```

## When it breaks

| symptom | cause |
|---|---|
| a node starts fine but sees no topics | `ROS_DISCOVERY_SERVER` or `FASTRTPS_DEFAULT_PROFILES_FILE` set in that shell, or a different `ROS_DOMAIN_ID` |
| everything worked, then nothing crosses machines | the WiFi AP stopped passing multicast (some do after a firmware update or on a guest network). Put all machines on the home network; the cable is the fallback |
| `.local` names don't resolve | mDNS blocked by the AP; `fleet.sh` accepts `LANGROBO_JETSON_HOST=192.168.1.15` / `LANGROBO_LAPTOP_HOST=...` overrides |
| the laptop drops off | suspend / WiFi power-save on the laptop — disable both |
