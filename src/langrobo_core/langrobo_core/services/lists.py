"""Household lists -- shopping, to-do, anything named. Pure zone.

Owner, 2026-10-04: "like Siri and Alexa". "Add milk and eggs to the shopping
list", "what's on my list?", "remove eggs", "clear the to-do list". One JSON
file next to the alarms (same lock + atomic write), so Studio and agent_node
see the same lists. Household lists were cut on 2026-09-06 with the rest of
the "real weight"; this is the small version: names -> items, nothing else.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

DEFAULT_LIST = "shopping"
_ALIASES = {"grocery": "shopping", "groceries": "shopping", "shopping list": "shopping",
            "todo": "to-do", "to do": "to-do", "todos": "to-do", "tasks": "to-do"}


def _path() -> Path:
    base = os.environ.get("LANGROBO_STATE_DIR") or str(Path.home() / ".local/state/langrobo")
    return Path(base) / "lists.json"


@contextmanager
def _locked():
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(p.read_text())
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
            yield data
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
            os.replace(tmp, p)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def list_name(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    n = re.sub(r"^(my|the)\s+", "", n)
    n = re.sub(r"\s+list$", "", n)
    return _ALIASES.get(n, n) or DEFAULT_LIST


def split_items(text: str) -> list[str]:
    """"milk, eggs and two loaves of bread" -> ["milk", "eggs", "two loaves of bread"]."""
    parts = re.split(r",|;|\band\b|\n", text or "")
    return [p.strip(" .") for p in parts if p.strip(" .")]


def add(name: str, text: str) -> tuple[list[str], list[str]]:
    """(added, already there). Case-insensitive: "Milk" is "milk"."""
    name = list_name(name)
    added, dupes = [], []
    with _locked() as data:
        items = data.setdefault(name, [])
        have = {i.lower() for i in items}
        for it in split_items(text):
            (dupes if it.lower() in have else added).append(it)
            if it.lower() not in have:
                items.append(it)
                have.add(it.lower())
    return added, dupes


def remove(name: str, text: str) -> tuple[list[str], list[str]]:
    """(removed, not found). An item matches if the spoken words are all in it."""
    name = list_name(name)
    removed, missing = [], []
    with _locked() as data:
        items = data.get(name, [])
        for want in split_items(text):
            words = want.lower().split()
            hit = next((i for i in items if all(w in i.lower() for w in words)), None)
            if hit:
                items.remove(hit)
                removed.append(hit)
            else:
                missing.append(want)
    return removed, missing


def show(name: str) -> list[str]:
    with _locked() as data:
        return list(data.get(list_name(name), []))


def clear(name: str) -> int:
    with _locked() as data:
        return len(data.pop(list_name(name), []))


def names() -> list[str]:
    with _locked() as data:
        return [k for k, v in data.items() if v]
