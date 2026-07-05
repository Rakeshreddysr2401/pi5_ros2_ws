#!/usr/bin/env python3
"""Bulk-ingest documents into the household knowledge base.

    python3 scripts/ingest_docs.py manual.pdf notes.md docs_dir/

IMPORTANT: the embedded Qdrant store is single-process. While the brain is
running it owns the store — this script detects that and tells you to either
stop the brain first (`sudo systemctl stop langrobo-brain`) or just send the
file to the robot's Telegram bot instead (the running brain ingests it live).
"""

import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "langrobo_core"))

from dotenv import load_dotenv

load_dotenv(os.path.expanduser(os.getenv("LANGROBO_ENV_FILE", "~/ros2_ws/.env")))

from langrobo_core.services import config as config_service   # noqa: E402
from langrobo_core.services import knowledge, memory           # noqa: E402

SUPPORTED = (".pdf", ".txt", ".md")


def _files(args: list[str]) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for a in args:
        p = pathlib.Path(a).expanduser()
        if p.is_dir():
            out += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in SUPPORTED]
        elif p.exists():
            out.append(p)
        else:
            print(f"skip (not found): {p}")
    return out


def main() -> int:
    files = _files(sys.argv[1:])
    if not files:
        print(__doc__)
        return 2

    settings = config_service.load_settings()
    mem = memory.init(settings.memory)
    deadline = time.time() + 120
    while not mem.available() and not mem.status()["error"] and time.time() < deadline:
        time.sleep(0.5)
    if not mem.available():
        err = mem.status()["error"] or "store did not come up"
        if "already accessed by another instance" in str(err) or "lock" in str(err).lower():
            print("The brain is running and owns the knowledge store.\n"
                  "Either: sudo systemctl stop langrobo-brain   (then rerun this)\n"
                  "Or:     send the file to the robot's Telegram bot instead.")
        else:
            print(f"Knowledge store unavailable: {err}")
        return 1

    total = 0
    for f in files:
        chunks, err = knowledge.ingest_file(f.name, f.read_bytes())
        print(f"{f.name}: " + (f"ERROR — {err}" if err else f"{chunks} chunk(s)"))
        total += chunks
    print(f"\nDone — {total} chunk(s) across {len(files)} file(s). "
          f"Restart the brain if you stopped it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
