# Disabled system services (headless SSH setup)

This machine (`rakhi24-desktop`, Ubuntu on Raspberry Pi) is operated exclusively
over SSH from a laptop. The desktop GUI and several unused daemons were
disabled on 2026-07-02 to free RAM/CPU for ROS2 + vision workloads. This file
lists exactly what changed and how to reverse each change if ever needed
(e.g. plugging in a monitor, needing Bluetooth, printing, etc.).

Script used: see bottom of this file for the exact commands.

## What was NOT touched (still active, do not disable)

- `NetworkManager.service`, `wpa_supplicant.service` — wlan0 is the only
  network path; this is how SSH reaches the Pi.
- `ssh.service` — obviously required.
- `avahi-daemon.service` — kept so the Pi stays reachable as
  `rakhi24-desktop.local` even if its DHCP IP changes.
- `pipewire.service`, `pipewire-pulse.service`, `wireplumber.service` (user
  services) — required for the TTS-speaking ROS2 topic (audio output).
- `dbus`, `polkit`, `systemd-*` core services — required for normal system
  operation.

## 1. GUI desktop (GDM + GNOME Shell)

**Why:** Full GNOME session (GNOME Shell, tracker file indexer, evolution
mail/calendar services, gvfs volume monitors, xdg-desktop-portals, IBus,
GNOME Settings Daemon) was running permanently, consuming several hundred
MB of RAM and CPU for a machine with no attached monitor, only used via SSH.

**Changed:**
- Default boot target: `graphical.target` → `multi-user.target` (boots to
  console, no display manager).
- Disabled + stopped: `gdm.service`, `gnome-remote-desktop.service`,
  `accounts-daemon.service`, `switcheroo-control.service`,
  `power-profiles-daemon.service`, `udisks2.service`.

**Effect:** No local GUI login screen; GNOME Shell and all its per-session
services (tracker-miner, evolution-*, gvfs-*, xdg-desktop-portal-*, IBus,
org.gnome.SettingsDaemon.*) stop automatically since nothing starts a
session anymore. SSH is unaffected.

**To re-enable (e.g. you plug in a monitor and want a local desktop again):**
```bash
sudo systemctl set-default graphical.target
sudo systemctl enable --now gdm.service gnome-remote-desktop.service \
  accounts-daemon.service switcheroo-control.service \
  power-profiles-daemon.service udisks2.service
sudo reboot   # or just: sudo systemctl isolate graphical.target
```

## 2. GNOME Remote Desktop (built-in RDP/VNC)

**Why:** Redundant with SSH; also tied to the GUI stack above.

**Changed:** Disabled + stopped `gnome-remote-desktop.service` (also listed
in section 1, included here since it's a distinct feature — RDP/VNC access,
not just login screen).

**To re-enable:**
```bash
sudo systemctl enable --now gnome-remote-desktop.service
```

## 3. Bluetooth

**Why:** No Bluetooth peripherals used with the robot.

**Changed:** Disabled + stopped `bluetooth.service`.

**To re-enable:**
```bash
sudo systemctl enable --now bluetooth.service
```

## 4. Printing (CUPS)

**Why:** No printer use case on a robot.

**Changed:** Disabled + stopped `cups.service`, `cups-browsed.service`
(and their socket/path activation units `cups.socket`, `cups.path`).

**To re-enable:**
```bash
sudo systemctl enable --now cups.service cups-browsed.service cups.socket cups.path
```

## 5. ModemManager

**Why:** No cellular modem hardware present.

**Changed:** Disabled + stopped `ModemManager.service`.

**To re-enable:**
```bash
sudo systemctl enable --now ModemManager.service
```

## 6. Tracing / crash-reporting daemons

**Why:** `lttng-sessiond` (kernel/userspace tracing) and `kerneloops` /
`apport` (automatic crash report collection + upload to Ubuntu) are not
needed for normal operation and add background overhead + telemetry.

**Changed:** Disabled + stopped `lttng-sessiond.service`,
`kerneloops.service`, `apport.service`.

**To re-enable:**
```bash
sudo systemctl enable --now lttng-sessiond.service kerneloops.service apport.service
```

## 7. OpenVPN (stub)

**Why:** `openvpn.service` was active but only running `/bin/true` — no
actual tunnel configs exist under `/etc/openvpn/client` or
`/etc/openvpn/server`. It was doing nothing.

**Changed:** Disabled + stopped `openvpn.service`.

**To re-enable (only relevant if you later add an actual VPN config):**
```bash
sudo systemctl enable --now openvpn.service
```

## 8. snapd

**Why:** The only installed snaps are GUI desktop apps (Firefox,
Thunderbird, snap-store, GNOME/mesa runtime bases) — nothing ROS2-related
depends on snapd. Verified with `snap list` before disabling.

**Changed:** Disabled + stopped `snapd.service`. Note: `snapd.socket` is
still present (socket-activation), so running `snap install/list/refresh`
manually will still transparently start snapd on demand — it just won't be
kept running permanently in the background.

**To re-enable (keep it always running again):**
```bash
sudo systemctl enable --now snapd.service
```

## Full re-enable (undo everything in this file)

```bash
sudo systemctl set-default graphical.target
sudo systemctl enable --now \
  gdm.service gnome-remote-desktop.service accounts-daemon.service \
  switcheroo-control.service power-profiles-daemon.service udisks2.service \
  bluetooth.service cups.service cups-browsed.service cups.socket cups.path \
  ModemManager.service lttng-sessiond.service kerneloops.service apport.service \
  openvpn.service snapd.service
sudo reboot
```

## Verifying health after any change

```bash
systemctl is-active ssh NetworkManager wpa_supplicant avahi-daemon
systemctl --user is-active pipewire pipewire-pulse wireplumber   # TTS audio
systemctl get-default
free -h
```

## Result

- Running services: ~31 → ~21
- Memory freed: GNOME Shell + session services no longer resident (was one
  of the largest consumers alongside ROS2/vision processes)
- No reboot was required — `--now` stopped everything immediately;
  `set-default` only affects the *next* boot.
