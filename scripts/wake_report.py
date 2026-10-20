#!/usr/bin/env python3
"""Did the wake word trigger? Read the wake log that stt_node writes.

    ./scripts/wake_report.py              # today: hits, misses, near-misses
    ./scripts/wake_report.py --days 3     # the last 3 days together
    ./scripts/wake_report.py -f           # live: one line per sentence heard
    ./scripts/wake_report.py --list 30    # the last 30 sentences, with decisions

What the decisions mean:
    wake             the name was in the sentence -> a turn
    follow-up        no name, but within the window after a turn -> a turn
    follow-up-limit  too many in a row without the name -> dropped
    ignored          no name, outside the window -> dropped
    stop             a stop word (no name needed)
    empty            the mic heard sound, the recogniser found no words
    open/held        require_wake: false (everything answered)
    llm-*            addressing_mode: llm (the Mac judged it)

A near-miss is an ignored sentence with a word close to the name
("Keno, come here"): probably a missed wake, not room talk. The log only
reports it -- the gate is not changed. Their clips (logs/wake/clips/, aplay
<file> to listen) are training material for the wake-word model.
"""
import argparse
import collections
import glob
import json
import os
import statistics
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(REPO, "logs/wake")
TURN = {"wake", "follow-up", "acoustic", "open", "llm-yes", "music-control"}


def files(days: int) -> list[str]:
    want = {time.strftime("%Y-%m-%d", time.localtime(time.time() - d * 86400)) for d in range(days)}
    return sorted(f for f in glob.glob(os.path.join(LOG_DIR, "wake-*.jsonl"))
                  if os.path.basename(f)[5:15] in want)


def read(paths) -> list[dict]:
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass                      # a line cut short by a restart
    return out


def show(e: dict) -> str:
    """Two lines for a turn (heard, then what the brain's LLM got), one otherwise."""
    d = e["decision"]
    mark = "✓" if d in TURN else "·" if d == "empty" else "✗"
    head = {"wake": "WAKE WORD", "follow-up": "follow-up"}.get(d, d)
    near = f"   <- near-miss {e['near']['word']!r} ({e['near']['ratio']})" if e.get("near") else ""
    clip = f"   [{e['clip']}]" if e.get("clip") else ""
    out = f"{e['when']}  {mark} {head:<15} heard: {e['text']!r}{near}{clip}"
    if "sent" in e:
        window = ""
        if d == "wake" and "window_s" in e:
            window = f"   (listening {e['window_s']:.0f} s, {e['follow_ups_left']} follow-ups without the name)"
        elif "follow_ups_left" in e:
            window = f"   ({e['follow_ups_left']} follow-ups left)"
        out += f"\n{'':10}  -> to LLM: {e['sent']!r}{window}"
    return out


def summary(entries: list[dict]) -> None:
    if not entries:
        print(f"no wake log yet in {LOG_DIR} -- is langrobo-voice running with wake_log_dir set?")
        return
    by = collections.Counter(e["decision"] for e in entries)
    spoken = [e for e in entries if e["decision"] != "empty"]
    print(f"{len(entries)} utterances ({len(spoken)} with words), "
          f"{entries[0]['when']} .. {entries[-1]['when']}")
    for decision, n in by.most_common():
        print(f"  {decision:<16} {n}")
    lat = [e["latency_ms"] for e in spoken if e.get("latency_ms")]
    if lat:
        print(f"  speech-to-text: median {statistics.median(lat):.0f} ms, worst {max(lat)} ms")

    wakes = [e for e in entries if e["decision"] == "wake"]
    print(f"\nwakes ({len(wakes)}):")
    for e in wakes[-10:]:
        print("  " + show(e))

    near = [e for e in entries if e.get("near") and e["decision"] not in TURN]
    print(f"\nnear-misses -- probably said the name, not heard as it ({len(near)}):")
    for e in near[-10:]:
        print("  " + show(e))
    words = collections.Counter(e["near"]["word"] for e in near)
    common = [(w, n) for w, n in words.most_common() if n >= 2]
    if common:
        print("\nhow the recogniser misspells the name (clips: wake-word training material):")
        for w, n in common:
            print(f"  {w!r:<14} x{n}")


def follow() -> None:
    print(f"watching {LOG_DIR} -- say 'Kino, ...' (Ctrl-C to stop)\n"
          "  ✓ = a turn (the '-> to LLM' line is exactly what the brain receives)\n"
          "  ✗ = dropped   · = noise, no words\n")
    path, pos = None, 0
    while True:
        today = os.path.join(LOG_DIR, time.strftime("wake-%Y-%m-%d.jsonl"))
        if today != path:
            path, pos = today, os.path.getsize(today) if os.path.exists(today) else 0
        if os.path.exists(path) and os.path.getsize(path) > pos:
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    try:
                        print(show(json.loads(line)), flush=True)
                    except ValueError:
                        pass
                pos = f.tell()
        time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=1, help="how many days back (default: today)")
    ap.add_argument("-f", "--follow", action="store_true", help="live, one line per sentence")
    ap.add_argument("--list", type=int, metavar="N", help="the last N sentences")
    args = ap.parse_args()
    try:
        if args.follow:
            follow()
        entries = read(files(args.days))
        if args.list:
            for e in entries[-args.list:]:
                print(show(e))
        else:
            summary(entries)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
