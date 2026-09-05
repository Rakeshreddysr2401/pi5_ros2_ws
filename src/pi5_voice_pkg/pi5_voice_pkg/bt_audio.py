"""Bluetooth audio routing for the Pi5 voice pair.

The nodes address ALSA devices through sounddevice, but a Bluetooth speaker is
not an ALSA card — it is a PipeWire node. So the wiring is indirect: connect the
speaker over bluez, make it PipeWire's default sink (and source, for HFP), and
point the node at the `pipewire` ALSA device. This module is that glue.

No rclpy, no sounddevice — just subprocess and parsing, so it is testable off
the robot and importable from either node.

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


# ── The one call the nodes make ─────────────────────────────────────────────

def ensure(mac: str, profile: str = "a2dp") -> dict:
    """Connect the speaker and point PipeWire at it.

    Returns a status dict describing exactly how far it got — the caller logs
    it and carries on with whatever audio device it already had. Never raises.
    """
    result = {"enabled": bool(mac), "mac": mac, "profile": profile,
              "connected": False, "sink": None, "source": None, "notes": []}
    if not mac:
        return result
    if not is_mac(mac):
        result["notes"].append(f"bt_mac {mac!r} is not a MAC address")
        return result

    ok, msg = bt_connect(mac)
    result["connected"] = ok
    result["notes"].append(f"connect: {msg}")
    if not ok:
        return result

    if profile == "hfp":
        ok, msg = set_profile(mac, "hfp")
        result["notes"].append(f"profile: {msg}")
    elif profile == "a2dp":
        # WirePlumber already picks A2DP on connect; only correct it if pactl
        # is around and something else got selected.
        if shutil.which("pactl"):
            set_profile(mac, "a2dp")

    status = wpctl_status()
    sink = find_bt_node(status, "Sinks", mac)
    if sink:
        set_default(sink["id"])
        result["sink"] = sink["name"]
    else:
        result["notes"].append("no PipeWire sink for this device yet")

    if profile == "hfp":
        source = find_bt_node(status, "Sources", mac)
        if source:
            set_default(source["id"])
            result["source"] = source["name"]
        else:
            result["notes"].append("no PipeWire source — is the card in HFP?")

    return result
