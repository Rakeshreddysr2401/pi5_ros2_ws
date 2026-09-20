#!/usr/bin/env python3
"""Guided pairing for a NEW Bluetooth speaker or headphones.

    ./scripts/bt_speaker.sh pair        # (no address) runs this

Already-paired devices never need this — switch them on and
audio_device_node connects them (PI5_VOICE.md). This is for the one human
step a new device needs: put it in pairing mode, pick it from the list, done.
No ROS, no sudo; uses the same bluez helpers as the node.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "pi5_voice_pkg"))
from pi5_voice_pkg import bt_audio  # noqa: E402

PARAMS = os.path.expanduser("~/ros2_ws/src/pi5_voice_pkg/config/voice_params.yaml")
SCAN_S = 15
_LOOKS_LIKE_MAC_NAME = re.compile(r"^([0-9A-F]{2}-){5}[0-9A-F]{2}$", re.I)


def say(msg=""):
    print(msg, flush=True)


def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        say()
        sys.exit(0)


def known_audio():
    out = []
    for d in bt_audio.known_devices():
        info = bt_audio.device_info(d["mac"]) | {"name": d["name"]}
        if info["audio"]:
            out.append(info)
    return out


def show_known(devices):
    if not devices:
        say("No speaker or headphones paired yet.")
        return
    say("Already paired (just switch them on — the robot connects them by itself):")
    for d in devices:
        state = "connected now" if d["connected"] else "off / out of range"
        mic = "speaker + mic" if d["mic"] else "speaker only"
        say(f"  - {d['name']}  [{mic}, {state}]")


def scan_for_new(known_macs):
    say(f"Scanning for {SCAN_S} seconds...")
    found = []
    for d in bt_audio.scan(SCAN_S):
        if d["mac"] in known_macs or _LOOKS_LIKE_MAC_NAME.match(d["name"]):
            continue
        info = bt_audio.device_info(d["mac"]) | {"name": d["name"]}
        if info["audio"]:
            found.append(info)
    return found


def prefer_in_config(mac):
    try:
        text = open(PARAMS).read()
    except OSError:
        say(f"(could not read {PARAMS} — add it to bt_devices by hand if you want it preferred)")
        return
    m = re.search(r"^(\s*bt_devices:\s*\[)([^\]]*)(\].*)$", text, re.M)
    if not m:
        say("(no bt_devices line found in voice_params.yaml — not changed)")
        return
    existing = [x.strip().strip('"\'') for x in m.group(2).split(",") if x.strip()]
    ordered = [mac] + [x for x in existing if x.upper() != mac.upper()]
    line = m.group(1) + ", ".join(f'"{x}"' for x in ordered) + m.group(3)
    open(PARAMS, "w").write(text[:m.start()] + line + text[m.end():])
    say(f"voice_params.yaml: bt_devices now prefers {mac} (restart langrobo-voice to apply).")


def wait_for_robot(mac, timeout_s=20):
    """Did audio_device_node switch to it? True if it became the default sink."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        sink = bt_audio.find_bt_node(bt_audio.wpctl_status(), "Sinks", mac)
        if sink and sink["default"]:
            return True
        time.sleep(1.0)
    return False


def main():
    rc, _ = bt_audio._run(["bluetoothctl", "show"])
    if rc != 0:
        say("Bluetooth is not available (is bluetooth.service running?).")
        sys.exit(1)

    known = known_audio()
    show_known(known)
    say()
    say("To add a NEW one: put it in pairing mode (usually hold its button until")
    say("the light blinks fast), then press Enter.  q = quit")
    if ask("> ").lower() == "q":
        return

    known_macs = {d["mac"] for d in known}
    while True:
        found = scan_for_new(known_macs)
        if found:
            break
        say("Nothing new that looks like a speaker/headphones was found.")
        say("Check it is in pairing mode and close to the robot.  Enter = scan again, q = quit")
        if ask("> ").lower() == "q":
            return

    say("Found:")
    for i, d in enumerate(found, 1):
        say(f"  {i}. {d['name']}")
    choice = ask("Which one? (number, q = quit) > ")
    if not choice.isdigit() or not 1 <= int(choice) <= len(found):
        return
    dev = found[int(choice) - 1]

    say(f"Pairing with {dev['name']}...")
    ok, msg = bt_audio.bt_pair(dev["mac"])
    if not ok:
        say(f"Pairing failed: {msg}")
        if "NotAuthorized" in msg or "AuthenticationFailed" in msg:
            say("If this keeps happening, run once:  sudo usermod -aG bluetooth $USER  (then log in again)")
        else:
            say("Make sure it is still in pairing mode and try again.")
        sys.exit(1)
    bt_audio.bt_trust(dev["mac"])
    say(f"Paired and trusted. From now on, switching {dev['name']} on is enough.")

    ok, _ = bt_audio.bt_connect(dev["mac"])
    say("Waiting for the robot to switch to it..." if ok else "Connect did not go through yet; the robot will keep trying.")
    if wait_for_robot(dev["mac"]):
        say(f"The robot is now using {dev['name']}.")
    else:
        say("Not switched yet — if voice is not running, start it (./scripts/run_voice.sh) and it will pick this device up.")

    if ask("Prefer this device when several are switched on? [y/N] > ").lower() == "y":
        prefer_in_config(dev["mac"])


if __name__ == "__main__":
    main()
