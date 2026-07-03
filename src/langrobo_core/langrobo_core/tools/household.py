"""Household lists & memory — pure zone (no rclpy).

Named lists ("shopping", "todo", …) plus free-form facts the household tells
the robot ("remember that Rakesh likes his coffee black"). Both persist in one
JSON file (default ~/.langrobo/household.json, override with
LANGROBO_HOUSEHOLD_FILE) and survive brain restarts.

Recall is deliberately NOT retrieval-based (no vector RAG / Mem0): a household
corpus is a few hundred short facts at most, which fits in the prompt — so
household_context() injects the whole block into chat's system prompt and the
LLM sees everything at once. No embedding infra, no silent recall misses, and
the block only re-prefills the llama.cpp cache when memory actually changes.
Upgrade path when per-person memory outgrows the prompt (roadmap phase 4):
embeddings via the llama.cpp server + a small local index.
"""

import json
import os
import threading
import time
from typing import Optional

from langchain_core.tools import tool

_DEFAULT_PATH = "~/.langrobo/household.json"

# Bounds keep the prompt block small and the robot honest about its memory.
_MAX_FACTS      = 150
_MAX_FACT_CHARS = 200
_MAX_LIST_ITEMS = 60


class HouseholdStore:
    """Thread-safe JSON persistence for lists + facts (worker thread only,
    but locked anyway — future producers may write from timers)."""

    def __init__(self, path: str | None = None):
        self._path = os.path.expanduser(
            path or os.getenv("LANGROBO_HOUSEHOLD_FILE", _DEFAULT_PATH))
        self._lock = threading.Lock()
        self._lists: dict[str, list[str]] = {}
        self._facts: list[dict] = []   # {"text": str, "t": epoch}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                data = json.load(f)
            self._lists = {k: list(v) for k, v in data.get("lists", {}).items()}
            self._facts = list(data.get("facts", []))
        except (OSError, ValueError, TypeError):
            pass  # missing or corrupt — start empty

    def _save(self) -> None:
        # Called with the lock held; write-then-rename so a crash mid-write
        # never corrupts the store.
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"lists": self._lists, "facts": self._facts}, f, indent=1)
        os.replace(tmp, self._path)

    # ── Lists ──────────────────────────────────────────────────────────────

    def update_list(self, name: str, add: list[str], remove: list[str],
                    clear: bool) -> tuple[list[str], list[str], list[str]]:
        """Apply changes; return (added, removed, not_found)."""
        name = name.strip().lower()
        with self._lock:
            items = [] if clear else self._lists.get(name, [])
            removed, not_found = [], []
            for r in remove:
                r = r.strip()
                # exact case-insensitive match first, then substring
                match = next((i for i in items if i.lower() == r.lower()), None) \
                    or next((i for i in items if r.lower() in i.lower()), None)
                if match:
                    items.remove(match)
                    removed.append(match)
                else:
                    not_found.append(r)
            added = []
            for a in add:
                a = a.strip()
                if a and a.lower() not in (i.lower() for i in items) \
                        and len(items) < _MAX_LIST_ITEMS:
                    items.append(a)
                    added.append(a)
            if items:
                self._lists[name] = items
            else:
                self._lists.pop(name, None)
            self._save()
            return added, removed, not_found

    def lists(self) -> dict[str, list[str]]:
        with self._lock:
            return {k: list(v) for k, v in self._lists.items()}

    # ── Facts ──────────────────────────────────────────────────────────────

    def remember(self, text: str) -> bool:
        """Store a fact; returns False when memory is full."""
        text = " ".join(text.split())[:_MAX_FACT_CHARS]
        with self._lock:
            if len(self._facts) >= _MAX_FACTS:
                return False
            self._facts.append({"text": text, "t": time.time()})
            self._save()
            return True

    def forget(self, about: str) -> list[str]:
        """Remove all facts containing `about` (case-insensitive); return them."""
        needle = about.strip().lower()
        with self._lock:
            hit  = [f for f in self._facts if needle in f["text"].lower()]
            if hit:
                self._facts = [f for f in self._facts if needle not in f["text"].lower()]
                self._save()
            return [f["text"] for f in hit]

    def facts(self) -> list[dict]:
        with self._lock:
            return list(self._facts)


_store: HouseholdStore | None = None


def get_store() -> HouseholdStore:
    global _store
    if _store is None:
        _store = HouseholdStore()
    return _store


def household_context() -> str:
    """Compact memory block injected into chat's system prompt.

    Empty string when nothing is stored. Sits AFTER the static prompt text so
    the llama.cpp prefix cache only re-prefills when memory changes."""
    store = get_store()
    lists, facts = store.lists(), store.facts()
    if not lists and not facts:
        return ""
    out = ["\n== HOUSEHOLD MEMORY (things you were told — answer from here directly) =="]
    for name, items in sorted(lists.items()):
        out.append(f"List '{name}': {', '.join(items)}")
    if facts:
        out.append("Facts:")
        for f in facts:
            date = time.strftime("%Y-%m-%d", time.localtime(f["t"]))
            out.append(f"- [{date}] {f['text']}")
    if len(facts) >= _MAX_FACTS - 10:
        out.append(f"(memory almost full: {len(facts)}/{_MAX_FACTS} facts — "
                   "suggest forgetting outdated ones)")
    return "\n".join(out) + "\n"


# ── Tools (bound to chat) ────────────────────────────────────────────────────

@tool
def update_list(list_name: str, add: Optional[list[str]] = None,
                remove: Optional[list[str]] = None, clear: bool = False) -> str:
    """Change a named household list ("shopping", "todo", ...): add and/or
    remove items, or clear=True to empty it. Current list contents are already
    in your HOUSEHOLD MEMORY — read them from there, don't call this to look."""
    added, removed, not_found = get_store().update_list(
        list_name, add or [], remove or [], clear)
    parts = []
    if clear:
        parts.append(f"cleared '{list_name.strip().lower()}'")
    if added:
        parts.append("added: " + ", ".join(added))
    if removed:
        parts.append("removed: " + ", ".join(removed))
    if not_found:
        parts.append("not on the list: " + ", ".join(not_found))
    return "; ".join(parts) if parts else "No changes made."


@tool
def remember(fact: str) -> str:
    """Permanently remember a household fact the user tells you, e.g.
    "Rakesh likes his coffee black" or "the spare key is in the blue drawer".
    Store the fact itself, concisely — not the user's phrasing."""
    if get_store().remember(fact):
        return f"Remembered: {fact}"
    return "Memory is full — ask the user what to forget first."


@tool
def forget(about: str) -> str:
    """Forget stored facts matching a phrase (case-insensitive substring).
    Returns what was forgotten so you can confirm it to the user."""
    gone = get_store().forget(about)
    if not gone:
        return f"Nothing stored about '{about}'."
    return "Forgot: " + "; ".join(gone)
