"""Bluetooth audio plumbing for the Pi5 voice nodes.

The voice nodes address ALSA devices through sounddevice, but a Bluetooth
speaker is not an ALSA card — it is a PipeWire node. So the wiring is
indirect: connect the device over bluez, make it PipeWire's default sink (and
source, for HFP), and let the nodes open the `pipewire` ALSA device. This
module is the glue; `audio_device_node` is the ONE process that calls it.
stt_node and tts_node never touch Bluetooth state — they just wait for
/voice/audio_ready (see PI5_VOICE.md).

No rclpy, no sounddevice — just subprocess and parsing, so it is testable off
the robot.

Profiles
--------
* **a2dp** — speaker only, 44.1kHz, aptX available. What a Boat Stone is for.
  WirePlumber selects it by default on connect, so nothing else is needed.
* **hfp** — the speaker's call mic as well, 8-16kHz mono. Switching profiles
  needs `pactl set-card-profile` (name-based); `wpctl set-profile` takes an
  INDEX whose meaning varies per card, which is not something to guess at.
  Without pactl this returns a clear message instead of silently doing nothing.

Everything degrades: a missing tool, an absent speaker or a refused connection
returns a status dict, never an exception (CLAUDE.md hard rule 4).
"""

import re
import shutil
import subprocess
import time

DEFAULT_TIMEOUT_S = 8.0

# pactl's profile names for a bluez card.
PROFILE_PACTL = {"a2dp": "a2dp-sink", "hfp": "headset-head-unit"}

_NODE_LINE = re.compile(r"^\s*(?P<default>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)\s*$")
_SECTION = re.compile(r"^(?P<name>Devices|Sinks|Sources|Streams|Filters):\s*$")
_MAC = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


# ── Naming ──────────────────────────────────────────────────────────────────

def is_mac(value: str) -> bool:
    return bool(_MAC.match((value or "").strip()))


def mac_suffix(mac: str) -> str:
    """AA:BB:CC:DD:EE:FF -> AA_BB_CC_DD_EE_FF, as PipeWire names it."""
    return (mac or "").upper().replace(":", "_").replace("-", "_")


def card_name(mac: str) -> str:
    return f"bluez_card.{mac_suffix(mac)}"


# ── wpctl status parsing ────────────────────────────────────────────────────

def parse_status(text: str) -> dict[str, list[dict]]:
    """Parse `wpctl status` into {section: [{id, name, default}]}.

    The tree drawing characters carry no information, so they are stripped and
    each row is read as "[*] <id>. <name> [vol: …]".
    """
    out: dict[str, list[dict]] = {}
    section = None
    for raw in (text or "").splitlines():
        line = raw.replace("│", " ").replace("├─", " ").replace("└─", " ")
        line = line.replace("┌", " ").replace("─", " ").strip()
        if not line:
            continue
        m = _SECTION.match(line)
        if m:
            section = m.group("name")
            out.setdefault(section, [])
            continue
        if section is None:
            continue
        # Drop the trailing "[vol: 1.00]" / "[vol: 1.00 MUTED]" decoration.
        line = re.sub(r"\s*\[vol:.*?\]\s*$", "", line)
        m = _NODE_LINE.match(line)
        if m:
            out[section].append({
                "id": int(m.group("id")),
                "name": m.group("name").strip(),
                "default": bool(m.group("default")),
            })
    return out


def find_node(status: dict, section: str, needle: str) -> dict | None:
    """First node in `section` whose name contains `needle` (case-insensitive)."""
    needle = (needle or "").lower()
    if not needle:
        return None
    for node in status.get(section, []):
        if needle in node["name"].lower():
            return node
    return None


_PROP = re.compile(r'^\s*\*?\s*(?P<key>[\w.]+)\s*=\s*"?(?P<val>[^"]*)"?\s*$')


def parse_inspect(text: str) -> dict[str, str]:
    """Properties out of `wpctl inspect <id>`."""
    props = {}
    for line in (text or "").splitlines():
        m = _PROP.match(line)
        if m:
            props[m.group("key")] = m.group("val").strip()
    return props


def node_properties(node_id: int) -> dict[str, str]:
    rc, out = _run(["wpctl", "inspect", str(node_id)])
    return parse_inspect(out) if rc == 0 else {}


def find_bt_node(status: dict, section: str, mac: str) -> dict | None:
    """Locate a Bluetooth node by MAC, whatever PipeWire chose to display.

    `wpctl status` shows node.description — the speaker's friendly name ("boAt
    Stone 650"), NOT its node.name ("bluez_output.D6_AA_BB_59_EF_B6.1"). So
    matching the MAC suffix against the visible name finds nothing, which is
    exactly what happened the first time this ran against real hardware.
    The authoritative field is api.bluez5.address, one `wpctl inspect` away.
    """
    suffix = mac_suffix(mac)
    hit = find_node(status, section, suffix)      # cheap path, if it ever works
    if hit:
        return hit
    want = (mac or "").upper()
    for node in status.get(section, []):
        props = node_properties(node["id"])
        if props.get("api.bluez5.address", "").upper() == want:
            return node
        if suffix in props.get("node.name", "").upper():
            return node
    return None


# ── Shell plumbing ──────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: float = DEFAULT_TIMEOUT_S) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, f"{cmd[0]} not installed"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out after {timeout}s"
    except Exception as exc:                      # never raise at a caller
        return 1, str(exc)


def wpctl_status() -> dict:
    rc, out = _run(["wpctl", "status"])
    return parse_status(out) if rc == 0 else {}


def bt_connect(mac: str) -> tuple[bool, str]:
    """Ask bluez to connect. Already-connected counts as success."""
    rc, out = _run(["bluetoothctl", "connect", mac], timeout=20.0)
    low = out.lower()
    if rc == 0 and ("successful" in low or "already" in low):
        return True, "connected"
    if "already connected" in low:
        return True, "already connected"
    return False, out.strip().splitlines()[-1] if out.strip() else f"rc={rc}"


def set_profile(mac: str, profile: str) -> tuple[bool, str]:
    """Switch the bluez card between A2DP and HFP. Needs pactl."""
    target = PROFILE_PACTL.get(profile)
    if target is None:
        return False, f"unknown profile {profile!r}"
    if not shutil.which("pactl"):
        return False, ("pactl not installed — A2DP works without it, but "
                       "switching to HFP needs `sudo apt install pulseaudio-utils`")
    rc, out = _run(["pactl", "set-card-profile", card_name(mac), target])
    return (rc == 0), (out.strip() or target)


def set_default(node_id: int) -> tuple[bool, str]:
    rc, out = _run(["wpctl", "set-default", str(node_id)])
    return (rc == 0), out.strip()


def set_volume(node_id: int, gain: float) -> tuple[bool, str]:
    """Set a node's volume. Gains above 1.0 are software amplification.

    The HFP mic comes back at its own level every time the speaker reconnects,
    so this has to run on each `ensure`, not once by hand.
    """
    rc, out = _run(["wpctl", "set-volume", str(node_id), f"{gain:.2f}"])
    return (rc == 0), out.strip()


def set_default_source_volume(gain: float) -> tuple[bool, str]:
    """Set the gain on whichever source is default right now.

    By id is not enough: the HFP profile switch re-creates the source node, so
    an id captured moments earlier no longer exists.
    """
    rc, out = _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", f"{gain:.2f}"])
    return (rc == 0), out.strip()


# ── bluez device discovery ─────────────────────────────────────────────────
# What bluetoothctl prints for `devices`, `devices Paired` and `info <mac>`.

_DEVICE_LINE = re.compile(r"^Device\s+(?P<mac>([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+(?P<name>.*)$")
_INFO_LINE = re.compile(r"^\s*(?P<key>[A-Za-z ]+?):\s*(?P<val>.*)$")

# Service UUIDs a device advertises. A2DP sink = it can play; HFP/HSP = it
# also has a call mic we can use over Bluetooth.
UUID_AUDIO_SINK = "0000110b"
UUID_HANDSFREE = "0000111e"
UUID_HEADSET = "00001108"


def parse_devices(text: str) -> list[dict]:
    """`bluetoothctl devices [...]` -> [{mac, name}]."""
    out = []
    for line in (text or "").splitlines():
        m = _DEVICE_LINE.match(line.strip())
        if m:
            out.append({"mac": m.group("mac").upper(), "name": m.group("name").strip()})
    return out


def parse_info(text: str) -> dict:
    """`bluetoothctl info <mac>` -> {name, paired, trusted, connected, audio, mic}.

    `audio` = advertises an A2DP sink (it is a speaker/headphone at all);
    `mic` = advertises HFP or HSP (it has a mic we can switch to).
    """
    info = {"name": "", "paired": False, "trusted": False, "connected": False,
            "audio": False, "mic": False}
    for raw in (text or "").splitlines():
        m = _INFO_LINE.match(raw)
        if not m:
            continue
        key, val = m.group("key").strip(), m.group("val").strip()
        if key == "Name":
            info["name"] = val
        elif key in ("Paired", "Trusted", "Connected"):
            info[key.lower()] = val.lower() == "yes"
        elif key == "Class":
            # A device seen in a scan but never paired has no UUID list yet;
            # its Class of Device (major class bits 8-12 == 0x04 Audio/Video)
            # and Icon are what bluez knows before pairing.
            try:
                cod = int(val.split()[0], 16)
                if (cod >> 8) & 0x1F == 0x04:
                    info["audio"] = True
            except ValueError:
                pass
        elif key == "Icon" and val.startswith("audio-"):
            info["audio"] = True
        elif key == "UUID":
            low = val.lower()
            if UUID_AUDIO_SINK in low:
                info["audio"] = True
            if UUID_HANDSFREE in low or UUID_HEADSET in low:
                info["mic"] = True
    return info


def known_devices() -> list[dict]:
    """Every device bluez remembers as paired OR trusted, de-duplicated.

    Both lists matter: a speaker that dropped its link key shows Paired: no
    but Trusted: yes (the boAt Stone does this after a reboot), and it is
    still the device the owner wants.
    """
    seen: dict[str, dict] = {}
    for scope in ("Paired", "Trusted"):
        rc, out = _run(["bluetoothctl", "devices", scope])
        if rc != 0:
            continue
        for d in parse_devices(out):
            seen.setdefault(d["mac"], d)
    if not seen:
        # bluez < 5.66 has no `devices <filter>`; fall back to the old verb.
        rc, out = _run(["bluetoothctl", "paired-devices"])
        for d in parse_devices(out if rc == 0 else ""):
            seen.setdefault(d["mac"], d)
    return list(seen.values())


def device_info(mac: str) -> dict:
    rc, out = _run(["bluetoothctl", "info", mac])
    info = parse_info(out if rc == 0 else "")
    info["mac"] = (mac or "").upper()
    return info


def scan(seconds: float = 15.0) -> list[dict]:
    """Discover nearby devices for `seconds`, then return everything bluez
    currently lists ({mac, name}) — known devices included; callers filter."""
    _run(["bluetoothctl", "--timeout", str(int(seconds)), "scan", "on"],
         timeout=seconds + 10.0)
    rc, out = _run(["bluetoothctl", "devices"])
    return parse_devices(out if rc == 0 else "")


def bt_trust(mac: str) -> tuple[bool, str]:
    rc, out = _run(["bluetoothctl", "trust", mac])
    return (rc == 0 and "succeeded" in out.lower()), out.strip()


def bt_pair(mac: str) -> tuple[bool, str]:
    """Re-pair a device that lost its link key. Needs the `bluetooth` group
    (or root) — bluez's DBus policy refuses pairing to anyone else."""
    rc, out = _run(["bluetoothctl", "pair", mac], timeout=30.0)
    low = out.lower()
    if rc == 0 and ("successful" in low or "already" in low):
        return True, "paired"
    return False, out.strip().splitlines()[-1] if out.strip() else f"rc={rc}"


def rank_devices(devices: list[dict], preferred: list[str],
                 current: str | None = None) -> list[dict]:
    """Order audio devices by who should be tried first.

    1. the device we are already using, if it is still connected (no flapping
       between two headphones that are both on);
    2. anything else already connected, preferred ones first;
    3. everything else, preferred order, then by name.
    Non-audio devices (keyboards, phones) never appear.
    """
    pref = [p.upper() for p in preferred]

    def key(d):
        mac = d["mac"].upper()
        is_current = 0 if (current and mac == current.upper() and d.get("connected")) else 1
        pref_idx = pref.index(mac) if mac in pref else len(pref)
        return (is_current, 0 if d.get("connected") else 1, pref_idx, d.get("name", ""))

    return sorted((d for d in devices if d.get("audio")), key=key)


def profile_for(device: dict, prefer_mic: bool) -> str:
    """hfp only when the device has a mic AND we want to use it."""
    return "hfp" if (prefer_mic and device.get("mic")) else "a2dp"


def await_bt_node(section: str, mac: str, timeout_s: float = 4.0) -> dict | None:
    """Poll for the PipeWire node of `mac` — it appears a moment after a
    connect or a profile switch, so a single status read often misses it."""
    deadline = time.monotonic() + timeout_s
    while True:
        node = find_bt_node(wpctl_status(), section, mac)
        if node or time.monotonic() >= deadline:
            return node
        time.sleep(0.3)
