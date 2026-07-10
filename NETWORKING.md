# Pi5 ↔ Jetson networking — "talk by name, meet at the Pi5, prefer the cable"

How the brain (Pi5) and the voice/vision box (Jetson) find each other and
exchange ROS2 topics (STT, TTS, camera) reliably across any network, with no
hand-edited config and no dependency on the internet.

> Replaces the old scheme where `fastdds_unicast.xml` was hand-edited with
> hardcoded IPs and "reverted from git" on every network change. That file is
> retired (see *Rollback*).

## The one idea

We identify the two machines by **name**, not by address numbers — exactly like
the Mac Mini LLM is already reached as `singireddys-mac-mini.local`. A tiny
always-on **"meeting point"** on the Pi5 (a Fast DDS *Discovery Server*) lets
each machine find the other by name, on whatever network they share.

- **Pi5 meeting point:** `rakhi24-desktop.local:11811` (server id 0).
- Both machines' ROS2 nodes are **clients** of it via
  `ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811`.
- Discovery is **unicast** (point-to-point), so it survives WiFi routers that
  block the "shout to find each other" multicast that plain ROS2 relies on.

## Behaviour (the rules we agreed)

| Situation | What happens | Internet? |
|---|---|---|
| **Cable only** (two boxes + wire, no WiFi/router) | Fixed cable IPs `192.168.2.10`/`.20`, they find each other by name over the wire. Fast. | No |
| **Home WiFi, no cable** | Both join WiFi, find each other by name automatically. | No |
| **Friend's / unknown WiFi** | Same — connect both, zero setup. | No |
| **WiFi that blocks them** | Safety net: **plug the cable back in.** Guaranteed. | No |

- 🔌 **Cable is king** — when the cable is plugged in, heavy traffic (voice +
  camera) uses the wire; WiFi is a warm backup.
- 📉 **Cable pulled mid-chat** → a ~2–5s hiccup, then it re-establishes over
  WiFi. Not seamless, by design (seamless would waste WiFi constantly).
- 🗣️ **Voice is protected** — on WiFi, camera images may slow down; talking
  stays responsive.

## The machines

| | Ethernet (cable) | WiFi | ROS runs in | mDNS name |
|---|---|---|---|---|
| **Pi5** | `eth0` 192.168.2.10 | `wlan0` 192.168.1.16 | host | `rakhi24-desktop.local` |
| **Jetson** | `enP8p1s0` 192.168.2.20 | `wlP1p1s0` 192.168.1.15 | `isaac_ros` container (**host net**) | `rakhi-jetson.local` |

Cable IPs (`192.168.2.x`) are static so the bare-cable case needs no router.
WiFi IPs come from DHCP — **irrelevant now**, because we never hardcode them.

## What runs where

**Pi5**
- `langrobo-discovery.service` → `scripts/run_discovery.sh` →
  `fastdds discovery -i 0 -p 11811` on all interfaces. Always-on, `Restart=always`.
- `langrobo-microros.service`, `langrobo-brain.service` — start *after* the
  meeting point; their run scripts export `ROS_DISCOVERY_SERVER=...:11811`.

**Jetson** (`isaac_ros` container, host networking)
- Hostname set to `rakhi-jetson` (was the useless default `localhost.localdomain`).
- Container image has **`libnss-mdns`** baked in so `.local` names resolve
  *inside* the container (the host already resolved them; the container did not).
- Container ROS env sets `ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811`
  and no longer uses `FASTRTPS_DEFAULT_PROFILES_FILE`.

## Why this and not the alternatives

- **Plain multicast discovery** (ROS2 default): zero-config on a clean cable,
  but many WiFi APs block/ratelimit multicast → fails on a friend's WiFi. That's
  exactly why the old config went unicast. Rejected.
- **Cloud mesh (Tailscale/WireGuard)**: great for going remote, but needs the
  internet to establish links → breaks the cable-only-no-internet requirement,
  and adds a dependency. Rejected for now (revisit if the fleet goes remote).

## Debug / verify

```bash
# On the Pi5, prove the link with DATA, not node lists (see gotcha below):
export ROS_DISCOVERY_SERVER=127.0.0.1:11811
ros2 topic echo --once /camera/color/image_raw/compressed \
    sensor_msgs/msg/CompressedImage --field format   # "jpeg" = Jetson→Pi5 works

systemctl status langrobo-discovery      # meeting point up?
getent ahostsv4 rakhi-jetson.local       # Jetson resolvable by name?
curl -s localhost:8090/status | jq .runtime.camera_frame_age_s  # brain's view
```

**`ros2 node list` with ROS_SUPER_CLIENT is a red herring on this Jazzy
build** — it returns empty even while pub/sub through the server works
perfectly (verified 2026-07-06). Never diagnose the link with node lists;
echo a continuously-published topic (camera) instead.

Which path is data using? `getent ahostsv4 rakhi24-desktop.local` returns the
cable IP (`192.168.2.10`) when the cable is up → traffic prefers the wire.

## State of the world — 2026-07-10 (cable "back", but carrier-only)

The Ethernet cable was re-connected 2026-07-10 (Pi5 eth0 192.168.2.10 ↔
Jetson enP8p1s0 192.168.2.20, routes present on both) — BUT it passes NO
data: carrier is up on both ends while the Jetson NIC shows **rx_packets=0
since boot** and ARP stays INCOMPLETE in both directions. Replace/reseat the
cable (or check what it is actually plugged into) before trusting it. Until
then WiFi remains the only working Pi5↔Jetson path.

What changed for the dual-link future (all committed in `~/robot`, 66819e9):

1. **`fleet_role.sh` resolves the Pi5 discovery server per voice launch**
   (`pi5_discovery()`): eth 192.168.2.10 preferred, wifi 192.168.1.16 next,
   mDNS-IPv4 last — so the moment the cable actually works, a voice restart
   uses it with no config change. Verified: a static two-server
   `ROS_DISCOVERY_SERVER="ip1;ip2"` list SILENTLY FAILS on this Fast DDS
   build (only entry 0 is honoured) — never use the list form for fallback.
2. **`fastdds_unicast.xml` whitelists BOTH Jetson interfaces** (wifi
   192.168.1.15 + eth 192.168.2.20) with peers on both networks — unreachable
   peers are retried harmlessly.
3. The Jetson **containers' default** env stays the wifi IP
   (`192.168.1.16:11811` — reserve that DHCP lease in the router); manual
   isaac_ros pipeline launches use it as-is.
4. **Laptop availability**: `rakhi24.local`/192.168.1.12 drops off the
   network intermittently (suspend / wifi power-save suspected) — disable
   suspend on the sim laptop for it to be a dependable fleet member.
   `fleet.sh` accepts `LANGROBO_LAPTOP_HOST`/`LANGROBO_JETSON_HOST` overrides
   when mDNS flakes.

## State of the world — 2026-07-06 (WiFi-only workarounds)

The Ethernet link is still physically dead AND the WiFi AP blocks
client↔client multicast, so cross-machine mDNS does not work at all right
now. Three workarounds are live; each is marked in-place with a comment and
should be removed when the cable is fixed:

1. **Pi5-local clients use loopback** (`scripts/run_brain.sh`,
   `run_microros.sh`, `dev.sh` → `ROS_DISCOVERY_SERVER=127.0.0.1:11811`).
   The name resolved IPv6-first on a WiFi-only boot and the server is UDPv4 —
   local registration silently failed. Loopback is always correct locally;
   the name is only needed cross-machine.
2. **Jetson pins the name to IPv4**: `/etc/hosts` on the Jetson host AND
   `extra_hosts:` in `~/robot/docker-compose.yml` (containers have their own
   /etc/hosts) map `rakhi24-desktop.local → 192.168.1.16` (Pi5 wlan0, DHCP —
   re-pin if the lease changes, or reserve the IP in the router).
3. The `ai_stack` image still bakes `FASTRTPS_DEFAULT_PROFILES_FILE=
   /config/fastdds_unicast.xml` (interface whitelist, currently the Jetson's
   WiFi IP 192.168.1.15). It happens to be harmless while that IP holds, but
   it binds DDS to ONE interface — update or blank it if IPs change.

## Rollback (if ever needed)

The old scheme is preserved in git. To revert:
1. `git checkout <pre-change> -- scripts/run_brain.sh scripts/run_microros.sh scripts/dev.sh fastdds_unicast.xml`
2. Reinstall the old units, `sudo systemctl disable --now langrobo-discovery`,
   restart brain + micro-ROS.
3. On the Jetson, restore the container env to `FASTRTPS_DEFAULT_PROFILES_FILE`
   and put valid IPs back in `/config/fastdds_unicast.xml`.

The cable being up is the always-available safety net during any transition.
