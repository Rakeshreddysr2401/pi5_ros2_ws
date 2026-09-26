#!/usr/bin/env python3
"""Switch the robot's wake word between the trained models — one command.

    ./scripts/wake_switch.py              # what is active now
    ./scripts/wake_switch.py rakhi        # wake on "Rakhi" (Telugu-trained)
    ./scripts/wake_switch.py mitra        # wake on "Mitra"
    ./scripts/wake_switch.py off          # no wake word: transcribe everything
    ./scripts/wake_switch.py mitra --threshold 0.5     # also set the threshold

Edits the three lines in voice_params.yaml that pick the model (path, name,
and the transcript-fallback aliases), then restarts langrobo-voice and shows
what the node loaded. Any .onnx in models/wake/ is a valid choice.

Only the WAKE WORD changes. What the robot calls itself when it speaks comes
from the persona in langrobo_core/prompts.py — see WAKE_WORD_INTEGRATION.md.
"""

import argparse
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAMS = os.path.join(REPO, "src/pi5_voice_pkg/config/voice_params.yaml")
WAKE_DIR = os.path.join(REPO, "src/langrobo_ros/models/wake")


def available() -> list[str]:
    if not os.path.isdir(WAKE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(WAKE_DIR) if f.endswith(".onnx"))


def current(text: str) -> dict:
    def find(key, default=""):
        m = re.search(rf"^\s*{key}:\s*(.+?)\s*(?:#.*)?$", text, re.M)
        return m.group(1).strip().strip('"\'') if m else default
    return {"detector": find("wake_detector"), "word": find("wake_word"),
            "model": find("wake_model_path"), "threshold": find("wake_threshold"),
            "require": find("require_wake")}


def set_line(text: str, key: str, value: str) -> str:
    """Replace `key:`'s value, keeping any trailing comment."""
    pattern = rf"^(\s*{key}:\s*)(?:[^#\n]*?)(\s*)(#.*)?$"
    new, n = re.subn(pattern, lambda m: f"{m.group(1)}{value}{m.group(2) or '   '}{m.group(3) or ''}".rstrip(),
                     text, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"could not find '{key}:' in {PARAMS}")
    return new


def restart_and_report() -> None:
    print("restarting langrobo-voice...")
    subprocess.run(["systemctl", "--user", "restart", "langrobo-voice"], check=False)
    time.sleep(12)
    out = subprocess.run(["journalctl", "--user", "-u", "langrobo-voice", "-n", "60", "-o", "cat"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "wake_detector =" in line or "audio ready:" in line:
            print("  " + line.split("]: ")[-1])
    if "wake_detector =" not in out:
        print("  (no wake_detector line yet — check: journalctl --user -u langrobo-voice -n 50 -o cat)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("word", nargs="?", help=f"one of: {', '.join(available()) or '(no models)'}, or 'off'")
    ap.add_argument("--threshold", type=float, help="wake trigger threshold (0-1)")
    ap.add_argument("--no-restart", action="store_true", help="edit the config only")
    args = ap.parse_args()

    text = open(PARAMS).read()
    if not args.word and args.threshold is None:
        cur = current(text)
        print(f"wake_detector : {cur['detector']}")
        print(f"wake word     : {cur['word']}  (threshold {cur['threshold']}, require_wake {cur['require']})")
        print(f"model         : {cur['model'] or '(none)'}")
        print(f"available     : {', '.join(available()) or '(none in models/wake/)'}")
        print("\nswitch with:  ./scripts/wake_switch.py <word>|off")
        return

    if args.word == "off":
        text = set_line(text, "wake_detector", "transcript_alias")
        text = set_line(text, "require_wake", "false")
        print("wake word OFF — every utterance will be transcribed and sent to the brain.")
    elif args.word:
        if args.word not in available():
            sys.exit(f"no model {args.word!r} in {WAKE_DIR} — have: {', '.join(available()) or '(none)'}")
        text = set_line(text, "wake_detector", "openwakeword")
        text = set_line(text, "wake_word", args.word)
        text = set_line(text, "wake_model_path", os.path.join(WAKE_DIR, f"{args.word}.onnx"))
        text = set_line(text, "wake_aliases", f'["{args.word}", "hey {args.word}"]')
        text = set_line(text, "require_wake", "true")
        print(f"wake word -> {args.word}")
    if args.threshold is not None:
        text = set_line(text, "wake_threshold", f"{args.threshold:g}")
        print(f"threshold -> {args.threshold:g}")

    open(PARAMS, "w").write(text)
    if not args.no_restart:
        restart_and_report()


if __name__ == "__main__":
    main()
