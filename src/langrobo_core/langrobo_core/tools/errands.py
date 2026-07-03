"""Errand ledger — "tell Mom X and let me know what she says". Pure zone.

The middleman flow's persistence: when a relayed message asks for a report
back (send_telegram_message(report_back=True)), an errand is recorded here.
When the recipient's next Telegram message arrives, agent_node frames the turn
with the open errand so the agent closes the loop — even hours later, after
history has trimmed. Same pattern as the reminder store: one JSON file,
thread-safe, consumed by agent_node.

Errands expire (24h) so a never-answered relay doesn't haunt future turns.
"""

import json
import os
import threading
import time
from dataclasses import asdict, dataclass

_DEFAULT_PATH = "~/.langrobo/errands.json"
_EXPIRY_S = 24 * 3600


@dataclass
class Errand:
    id: int
    asked_via: str      # "voice" | "telegram" — where to report back
    asked_by: str       # sender name for telegram; "the user" for voice
    sent_to: str        # allowlisted member the relay went to
    gist: str           # short summary of what was relayed
    created: float


class ErrandStore:
    """Thread-safe, JSON-persisted open-errand list (tool + worker thread)."""

    def __init__(self, path: str | None = None):
        self._path = os.path.expanduser(
            path or os.getenv("LANGROBO_ERRANDS_FILE", _DEFAULT_PATH))
        self._lock = threading.Lock()
        self._next_id = 1
        self._errands: list[Errand] = []
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                data = json.load(f)
            self._next_id = data.get("next_id", 1)
            self._errands = [Errand(**e) for e in data.get("errands", [])]
        except (OSError, ValueError, TypeError):
            pass  # missing or corrupt file — start empty

    def _save(self) -> None:
        # Called with the lock held. Write-then-rename so a crash mid-write
        # never corrupts the store.
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"next_id": self._next_id,
                       "errands": [asdict(e) for e in self._errands]}, f, indent=1)
        os.replace(tmp, self._path)

    def _expire(self) -> None:
        # Called with the lock held.
        cutoff = time.time() - _EXPIRY_S
        live = [e for e in self._errands if e.created > cutoff]
        if len(live) != len(self._errands):
            self._errands = live
            self._save()

    def add(self, asked_via: str, asked_by: str, sent_to: str, gist: str) -> Errand:
        with self._lock:
            self._expire()
            e = Errand(id=self._next_id, asked_via=asked_via, asked_by=asked_by,
                       sent_to=sent_to, gist=gist[:200], created=time.time())
            self._next_id += 1
            self._errands.append(e)
            self._save()
            return e

    def pop_for_sender(self, sender_name: str) -> list[Errand]:
        """All open errands relayed TO this person — their message is the
        answer. Popped: one reply closes the loop; a wrong guess costs one
        follow-up question, not a stuck ledger."""
        with self._lock:
            self._expire()
            want = sender_name.casefold()
            hits = [e for e in self._errands if e.sent_to.casefold() == want]
            if hits:
                self._errands = [e for e in self._errands if e.sent_to.casefold() != want]
                self._save()
            return hits


# Module-level singleton — same pattern as the reminder store.
_store: ErrandStore | None = None


def get_store() -> ErrandStore:
    global _store
    if _store is None:
        _store = ErrandStore()
    return _store
