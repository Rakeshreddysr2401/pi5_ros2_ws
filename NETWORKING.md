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
# On the Pi5 (any shell), see the whole graph through the meeting point:
export ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811 ROS_SUPER_CLIENT=1
ros2 node list          # should list both Pi5 and Jetson nodes
ros2 topic list         # /voice/*, /audio/*, camera topics

systemctl status langrobo-discovery      # meeting point up?
getent ahostsv4 rakhi-jetson.local       # Jetson resolvable by name?
```

Which path is data using? `getent ahostsv4 rakhi24-desktop.local` returns the
cable IP (`192.168.2.10`) when the cable is up → traffic prefers the wire.

## Rollback (if ever needed)

The old scheme is preserved in git. To revert:
1. `git checkout <pre-change> -- scripts/run_brain.sh scripts/run_microros.sh scripts/dev.sh fastdds_unicast.xml`
2. Reinstall the old units, `sudo systemctl disable --now langrobo-discovery`,
   restart brain + micro-ROS.
3. On the Jetson, restore the container env to `FASTRTPS_DEFAULT_PROFILES_FILE`
   and put valid IPs back in `/config/fastdds_unicast.xml`.

The cable being up is the always-available safety net during any transition.
