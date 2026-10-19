"""Pure helpers behind /audio/cmd: volume arithmetic and "which device did the
owner mean?". No rclpy, no subprocess -- audio_device_node does the shelling.

Owner, 2026-10-04: "if I decrease or increase volume it needs to work", and
connect Bluetooth devices "easily" -- by name, the way people say it ("my
buds", "the boAt"), not by MAC address. docs/voice/ASSISTANT_SCENARIOS.md S5-S7.
"""

from __future__ import annotations

import re

# One "louder" / "quieter" without a number moves this much. 10 points is a
# step people hear; 5 is barely noticeable on the Stone.
DEFAULT_STEP = 10
# Above 100 % PipeWire amplifies in software and the Stone distorts.
MAX_PERCENT = 100

_VOLUME = re.compile(r"Volume:\s*([0-9.]+)(\s*\[MUTED\])?", re.I)


def parse_volume(text: str) -> tuple[int | None, bool]:
    """`wpctl get-volume <id>` -> (percent, muted). (None, False) if unreadable."""
    m = _VOLUME.search(text or "")
    if not m:
        return None, False
    return round(float(m.group(1)) * 100), bool(m.group(2))


def volume_target(current: int | None, set_to: int | None = None,
                  change: int | None = None) -> int:
    """The volume to set, 0..MAX_PERCENT. `set_to` wins over `change`; a
    change of 0 is a no-op. An unreadable current level counts as 50."""
    if set_to is not None:
        return max(0, min(MAX_PERCENT, int(set_to)))
    base = 50 if current is None else int(current)
    return max(0, min(MAX_PERCENT, base + int(change or 0)))


_WORD = re.compile(r"[a-z0-9]+")
# Said about devices, not part of their names.
_FILLER = {"my", "the", "a", "an", "to", "bluetooth", "device", "please", "connect",
           "use", "switch", "pair", "new", "on", "with"}


def _words(s: str) -> set[str]:
    return {w for w in _WORD.findall((s or "").lower()) if w not in _FILLER}


def match_device(spoken: str, devices: list[dict]) -> tuple[dict | None, list[dict]]:
    """Which of `devices` ({mac, name, ...}) did "spoken" mean?

    Returns (the one device, []) when the answer is clear, or (None, the
    tied candidates) when two fit equally ("connect my buds" with two pairs
    of buds), or (None, []) when nothing fits. A MAC address matches exactly.
    Scoring: exact name > every spoken word in the name > most shared words,
    with "buds"/"bud", "speaker"/"speakers" treated alike.
    """
    spoken = (spoken or "").strip()
    if not spoken or not devices:
        return None, []
    for d in devices:
        if d.get("mac", "").upper() == spoken.upper():
            return d, []

    def norm(ws: set[str]) -> set[str]:
        return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in ws}

    want = norm(_words(spoken))
    if not want:
        return None, []
    scored = []
    for d in devices:
        name = d.get("name", "")
        have = norm(_words(name))
        if name.lower() == spoken.lower():
            score = 100
        elif want <= have:
            score = 50 + len(want)
        else:
            shared = want & have
            # a partial word counts too: "boat" in "boAt", "oneplus" in "OnePlus Buds"
            partial = {w for w in want for h in have if len(w) >= 3 and (w in h or h in w)}
            score = 10 * len(shared) + 5 * len(partial - shared)
        if score:
            scored.append((score, d))
    if not scored:
        return None, []
    scored.sort(key=lambda t: -t[0])
    best = scored[0][0]
    tied = [d for s, d in scored if s == best]
    return (tied[0], []) if len(tied) == 1 else (None, tied)


def describe_devices(devices: list[dict], active_mac: str | None) -> list[dict]:
    """The list the brain sees: name, connected, in use -- nothing else."""
    out = []
    for d in devices:
        out.append({"name": d.get("name") or d.get("mac"), "mac": d.get("mac"),
                    "connected": bool(d.get("connected")),
                    "in_use": bool(active_mac) and d.get("mac", "").upper() == active_mac.upper()})
    return out
