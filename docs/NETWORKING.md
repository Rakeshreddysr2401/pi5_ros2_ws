# Networking — how the four machines find each other

Short version: **every ROS 2 process on every machine uses plain DDS
multicast on `ROS_DOMAIN_ID=0`, and every machine is reached by its mDNS
name.** No discovery server, no IP lists. Since 2026-11-17 the **Jetson <-> Pi 5
robot traffic runs on a direct Ethernet cable**, not WiFi (one XML profile on the
Pi 5 does that, see "The Jetson <-> Pi 5 cable" below). WiFi is still used for
the Mac Mini (LLM/VLM/STT), the internet (web search, api.sarvam.ai), the phone
teleop page, the RViz laptop and ssh.

## The machines

| machine | mDNS name | WiFi IP (DHCP unless noted) | ROS runs in | what it does |
|---|---|---|---|---|
| Pi 5 | `rakhi24-desktop.local` | 192.168.1.16 (fixed); cable `eth0` 10.0.0.2 | host (systemd) | brain, voice, micro-ROS agent, teleop :8091 |
| Jetson Orin | `rakhi-jetson.local` | 192.168.1.15; cable `enP8p1s0` 10.0.0.1 | `rover` container, host network | camera, lidar, pose, slam, nvblox, nav2, VLM bridge |
| Mac Mini | `singireddys-mac-mini.local` | moves | — (HTTP only) | llama.cpp LLM + VLM on :8080 |
| Laptop | — (user `chotesh`) | moves (192.168.1.9 on 2026-10-18) | Isaac Sim's bundled ROS (no install) | the twin (Isaac Sim, domain 42; announces itself on `/sim/hardware`) and RViz (`./sim rviz [real]`) |
| D555 camera | — | USB 3 to the Jetson since 2026-11-16 (was 192.168.11.55 PoE; `CAM_LINK=eth` fallback) | inside the `rover` container (librealsense over USB) | depth + IR + IMU |
| ESP32 | — | USB cable to the Pi 5 | micro-ROS → Pi 5 serial `/dev/ttyUSB0` 115200 (was WiFi UDP 8888 until 2026-10-07) | wheels |

## The Jetson <-> Pi 5 cable (since 2026-11-17)

Before this, robot traffic went over WiFi and the Jetson's signal decided how
well everything worked (2026-10-09: -72 dBm / 6 Mbit/s, `fleet.sh check` lost
every Jetson topic). Now:

| | Jetson | Pi 5 |
|---|---|---|
| port | `enP8p1s0` | `eth0` |
| NetworkManager profile | `kino-link` | `kino-link` (was `netplan-eth0`, 192.168.2.10, unused) |
| address | `10.0.0.1/24` (+ `192.168.11.70/24` kept for the D555 Ethernet fallback) | `10.0.0.2/24` |
| MTU | 1500 | 1500 |
| default route | never (internet stays on WiFi) | never |

Measured 2026-11-17: 935 Mbit/s on the cable vs 135 Mbit/s on WiFi, ping 0.3 ms;
on domain 0 the Jetson sees all 7 Pi 5 nodes and `/sonar/left` at 15.2 Hz (same as
on the Pi 5).

**MTU stays 1500.** The Pi 5's `macb` driver only changes MTU while the port is
down; NetworkManager sets it while up and logs "failure to set MTU". 9000 worked by
hand on 2026-10-07 (`ip link set eth0 down; ... mtu 9000; ... up`), but the Pi 5
gets results and compressed images, not raw camera streams, so 1500 is plenty.

**Why the Pi 5 needs an XML profile and a routing rule.** Fast DDS sends every
message to *every* address the other side announces. Measured with a 4 MB/s test
topic: Pi 5 -> Jetson went over the cable *and* twice over WiFi. Two pieces fix it:

1. `scripts/fastdds_cable.xml`: UDP only on 10.0.0.2 + loopback (shared memory for
   nodes on this Pi). The Pi 5 then announces only its cable address, so the Jetson
   sends to it over the cable (Jetson -> Pi 5: 100 % cable, 0 on WiFi).
2. A policy rule in the Pi 5's `kino-link` profile: anything sent *from* 10.0.0.2
   may only leave through the cable (`from 10.0.0.2 lookup 100`; table 100 =
   10.0.0.0/24 on eth0 + unreachable default). Without it the Pi 5 still sent to the
   Jetson's WiFi address through WiFi. With it: Pi 5 -> Jetson 100 % cable, WiFi
   1 kB/s (background). Fast DDS's `ignore_non_matching_locators` did not help.

`kino_dds` (scripts/ros_env.sh) uses the profile only when **domain 0** and
**eth0 has 10.0.0.2** and `KINO_LINK` is not `wifi`. So the twin (domain 42, the
sim laptop is on WiFi) is unchanged, and with the cable out at start the Pi 5
falls back to WiFi. Every run script goes through `kino_dds`, and so does
`~/langrobo_teleop/run_teleop.sh` (not in git, changed 2026-11-17, backup
`run_teleop.sh.bak-20261117`). After changing any of this, restart the services:
`sudo systemctl restart langrobo-brain langrobo-sonar langrobo-teleop` and
`systemctl --user restart langrobo-voice`.

**Cost:** a machine that is only on WiFi (the RViz laptop) no longer sees the
Pi 5's own topics on domain 0 (sonar, brain). It still sees everything from the
Jetson. To get them for one run: `KINO_LINK=wifi` for that service.

Undo: `sudo nmcli con delete kino-link` on both (then recreate the old DHCP /
192.168.11.70 profiles) and drop the `if` block from `kino_dds`.

Not tested yet: WiFi switched off on both (the owner will say when), and a full
`./rover up` + driving with the Pi 5 on the cable.

## The rules

1. **`ROS_DOMAIN_ID=0` and `ROS_DISCOVERY_SERVER` unset — everywhere.** Go through
   `kino_dds`; don't set `FASTDDS_DEFAULT_PROFILES_FILE` by hand. The
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

The old `langrobo-discovery.service` (a Fast DDS discovery server on :11811,
the pre-2026-07-16 scheme) was removed from the repo on 2026-10-18. On a Pi
installed before that, re-run `./scripts/install_systemd.sh` (it disables and
deletes the old unit) or run `sudo systemctl disable --now langrobo-discovery`.

## Checking the link

Prove it with **data**, not node lists:

```bash
# On the Pi 5 (same env as the brain):
source ~/ros2_ws/scripts/ros_env.sh; unset ROS_DISCOVERY_SERVER; export ROS_DOMAIN_ID=0
ros2 topic hz /odom                      # ~20 Hz = the Jetson's fused pose reaches the Pi 5
echo $FASTDDS_DEFAULT_PROFILES_FILE      # .../fastdds_cable.xml = on the cable
ping -c3 10.0.0.1                        # cable up?
awk '/eth0|wlan0/{print $1, "tx", $10}' /proc/net/dev   # run twice: robot traffic should grow eth0, not wlan0
ros2 topic hz /camera/color/image_raw/compressed   # only while the brain subscribes
curl -s localhost:8090/status | jq .runtime.camera_frame_age_s   # the brain's own view

getent ahostsv4 rakhi-jetson.local       # Jetson resolvable?
curl -s http://singireddys-mac-mini.local:8080/health   # LLM up?
```

## When it breaks

| symptom | cause |
|---|---|
| a node starts fine but sees no topics | `ROS_DISCOVERY_SERVER` or `FASTRTPS_DEFAULT_PROFILES_FILE` set in that shell, or a different `ROS_DOMAIN_ID`, or a hand-set `FASTDDS_DEFAULT_PROFILES_FILE` — use `kino_dds` |
| Pi 5 topics missing on the RViz laptop | expected since 2026-11-17: the Pi 5 talks ROS only on the cable (see above) |
| Jetson lost the Pi 5 after the cable was unplugged | the Pi 5 picks cable/WiFi when each service starts: re-plug, or restart the services to fall back to WiFi |
| everything worked, then nothing crosses machines | the WiFi AP stopped passing multicast (some do after a firmware update or on a guest network). Put all machines on the home network; the cable is the fallback |
| `.local` names don't resolve | mDNS blocked by the AP; `fleet.sh` accepts `LANGROBO_JETSON_HOST=192.168.1.15` / `LANGROBO_LAPTOP_HOST=...` overrides |
| the laptop drops off | suspend / WiFi power-save on the laptop — disable both |
