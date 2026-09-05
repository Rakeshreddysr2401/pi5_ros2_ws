"""LangGraph Server client — the dev-mode counterpart to the in-process graph.

Pure zone (no rclpy). agent_node builds and invokes the graph itself; in dev
mode `langgraph dev` owns the graph and serves it over HTTP, so voice needs a
client instead of a builder. This module is that client and nothing else: it
turns one utterance into one server run and yields normalised events. Whoever
calls it decides what a Token means (speak it, print it, log it).

Design notes
------------
* **Server-side history.** A LangGraph thread carries the checkpointer, so each
  turn sends only the NEW human message — the MessagesState reducer appends it.
  No local history list, no trimming (that is agent_node's job because it has no
  checkpointer).
* **Delta-safe tokens.** The server may send message chunks cumulatively
  (`messages/partial`) or as deltas (`messages` tuples) depending on stream mode
  and version. `_TextTracker` emits the suffix either way, so we never
  double-speak a sentence.
* **Degrade, never crash** (hard rule 4): a server that is down, slow or
  speaking an unknown event shape yields a TurnError, never an exception.
* **Two ways in.** `stream_turn` DRIVES a turn (voice). `iter_new_runs` +
  `join_run` WATCH for turns somebody else started — the Studio browser box,
  another client — so the same reply can be spoken. Runs we started ourselves
  are filtered out, so a voice turn is never spoken twice.

Transport is injectable (`client_factory`) so tests run without a server.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:2024"
# The graph key in langgraph.json ("graphs": {"agent": "./graph_studio.py:graph"}).
DEFAULT_ASSISTANT = "agent"
# The supervisor never emits user-facing text (grammar-forced handover), so its
# tokens are noise on the speaker. agent_node achieves this with a per-agent
# streaming=False override; the server has no such override, so we filter here.
DEFAULT_SKIP_NODES = ("supervisor",)


# ── Config ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StudioConfig:
    """Where the dev server is and how a turn is run against it."""

    url: str = DEFAULT_URL
    assistant_id: str = DEFAULT_ASSISTANT
    # persistent: one thread for the session (follow-ups keep context).
    # per_turn:   a fresh thread every utterance (clean-room A/B of one prompt).
    thread_mode: str = "persistent"
    connect_timeout_s: float = 5.0
    turn_timeout_s: float = 180.0
    skip_nodes: tuple[str, ...] = DEFAULT_SKIP_NODES

    @classmethod
    def from_env(cls) -> "StudioConfig":
        """For non-ROS callers (tests, scripts). The node uses ROS params."""
        return cls(
            url=os.getenv("LANGROBO_STUDIO_URL", DEFAULT_URL),
            assistant_id=os.getenv("LANGROBO_STUDIO_ASSISTANT", DEFAULT_ASSISTANT),
            thread_mode=os.getenv("LANGROBO_STUDIO_THREAD_MODE", "persistent"),
        )


# ── Normalised turn events ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Token:
    """One piece of new assistant text, already de-duplicated."""
    text: str
    node: str = ""


@dataclass(frozen=True)
class Values:
    """A full graph state snapshot (stream_mode=values)."""
    state: dict


@dataclass(frozen=True)
class TurnError:
    """The turn failed. `recoverable` False means the server looks absent."""
    message: str
    recoverable: bool = True


TurnEvent = Token | Values | TurnError


# ── Cumulative-or-delta token de-duplication ────────────────────────────────

class _TextTracker:
    """Emit only text not yet seen for a given message id.

    Handles both wire shapes without being told which one it is: if the incoming
    text extends what we already have it is cumulative (emit the suffix), else it
    is a delta (emit it whole and append).
    """

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def delta(self, msg_id: str, text: str) -> str:
        prev = self._seen.get(msg_id, "")
        if not text or text == prev:
            return ""
        if text.startswith(prev):
            self._seen[msg_id] = text
            return text[len(prev):]
        self._seen[msg_id] = prev + text
        return text

    def reset(self) -> None:
        self._seen.clear()


# ── Message-shape helpers ───────────────────────────────────────────────────

def _content_text(content) -> str:
    """Text out of a message content field — plain string or block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def reply_from_state(state: dict | None) -> str | None:
    """The current turn's assistant text out of a `values` snapshot.

    The watch path needs this because `join_stream` does NOT replay a run's
    token stream to a late joiner — only `values` events arrive (measured
    against langgraph-sdk 0.4.2), so a turn typed into the Studio box has to be
    spoken from its final state instead of token by token.

    Same walk as agent_node._extract_response: stop at the human message, so a
    turn that produced no text cannot re-speak the previous reply.
    """
    for msg in reversed((state or {}).get("messages") or []):
        if not isinstance(msg, dict):
            continue
        kind = str(msg.get("type") or msg.get("role") or "")
        if kind in ("human", "user"):
            return None
        if kind in ("tool", "system"):
            continue
        text = _content_text(msg.get("content")).strip()
        if text:
            return text
    return None


def _iter_messages(data) -> Iterator[tuple[dict, dict]]:
    """Yield (message, metadata) pairs from any of the server's message shapes.

    Three shapes are in the wild and all three appear across stream modes and
    server versions, so we accept all of them rather than pinning one:
      - a single message dict
      - [message, metadata]           (messages / messages-tuple)
      - [message, message, ...]       (messages/partial)
    """
    if isinstance(data, dict):
        if "content" in data or "type" in data:
            yield data, {}
        return
    if not isinstance(data, list) or not data:
        return
    head = data[0]
    if not isinstance(head, dict):
        return
    if (len(data) == 2 and isinstance(data[1], dict)
            and "content" not in data[1] and "type" not in data[1]):
        yield head, data[1]
        return
    for item in data:
        if isinstance(item, dict):
            yield item, {}


# ── Client ──────────────────────────────────────────────────────────────────

class StudioClient:
    """One LangGraph Server conversation, driven one utterance at a time.

    Thread-safety: `stream_turn` is not reentrant — the caller (one worker
    thread, as in agent_node) runs turns serially. Thread creation is locked
    because a status query can race the first turn.
    """

    def __init__(self, cfg: StudioConfig,
                 client_factory: Callable[[], object] | None = None) -> None:
        self._cfg = cfg
        self._factory = client_factory or self._default_factory
        self._client = None
        self._thread_id: str | None = None
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._turns = 0
        # Run ids this client started — the watcher skips them so a voice turn
        # is not spoken a second time by the watch path.
        self._own_runs: set[str] = set()
        self._seen_runs: set[str] = set()
        # Run ids are only known once the run's metadata event arrives, so
        # `_own_runs` alone is a RACE: the watcher's poll can see our own run
        # first and join it (observed live 2026-09-05 — the watcher grabbed a
        # mic turn and would have spoken the reply twice on different timing).
        # These two make the guard deterministic: while we are driving a turn,
        # and briefly after, every run on OUR thread is ours by construction.
        self._driving = 0
        self._drove_at = 0.0

    # ── Transport ───────────────────────────────────────────────────────────

    def _default_factory(self):
        from langgraph_sdk import get_sync_client
        return get_sync_client(url=self._cfg.url, timeout=self._cfg.turn_timeout_s)

    def _get_client(self):
        if self._client is None:
            self._client = self._factory()
        return self._client

    # ── Thread lifecycle ────────────────────────────────────────────────────

    def ensure_thread(self) -> str | None:
        """Create the conversation thread if needed. None if the server is down."""
        with self._lock:
            if self._thread_id and self._cfg.thread_mode == "persistent":
                return self._thread_id
            try:
                thread = self._get_client().threads.create()
                self._thread_id = thread["thread_id"] if isinstance(thread, dict) else thread.thread_id
                self._last_error = None
                logger.info("Studio thread %s (%s)", self._thread_id, self._cfg.url)
                return self._thread_id
            except Exception as exc:
                self._last_error = str(exc)
                self._client = None          # force a reconnect next attempt
                logger.warning("Studio thread create failed (%s): %s", self._cfg.url, exc)
                return None

    def pin_thread(self, thread_id: str) -> None:
        """Speak into an EXISTING conversation (e.g. the one open in the Studio
        browser tab) instead of creating a private one."""
        with self._lock:
            self._thread_id = thread_id

    def reset_thread(self) -> None:
        """Drop the conversation — the next turn starts with empty history."""
        with self._lock:
            self._thread_id = None

    # ── One turn ────────────────────────────────────────────────────────────

    def stream_turn(self, text: str, *, active_agent: str | None = None,
                    channel: str = "voice",
                    interrupted: Callable[[], bool] | None = None
                    ) -> Iterator[TurnEvent]:
        """Run one utterance against the server, yielding normalised events.

        `interrupted` is polled between events; when it returns True the run is
        cancelled server-side and the generator stops. Never raises.
        """
        thread_id = self.ensure_thread()
        if thread_id is None:
            yield TurnError(f"LangGraph server unreachable at {self._cfg.url}",
                            recoverable=False)
            return

        if self._cfg.thread_mode == "per_turn":
            self.reset_thread()

        payload = {
            "messages": [{"role": "user", "content": text}],
            "active_agent": active_agent or "chat",
            "channel": channel,
            "sender_name": None,
            "sender_role": None,
        }
        tracker = _TextTracker()
        self._turns += 1
        self._driving += 1

        try:
            stream = self._get_client().runs.stream(
                thread_id,
                self._cfg.assistant_id,
                input=payload,
                stream_mode=["messages", "values"],
            )
            yield from self._consume(stream, thread_id, interrupted, tracker=tracker)
            self._last_error = None

        except Exception as exc:
            self._last_error = str(exc)
            self._client = None
            logger.warning("Studio run failed: %s", exc)
            yield TurnError(f"the dev server dropped the turn ({exc})")

        finally:
            self._driving -= 1
            self._drove_at = time.time()

    # ── Watching runs somebody else started ─────────────────────────────────

    def iter_new_runs(self, poll_s: float = 0.5,
                      stop: Callable[[], bool] | None = None,
                      skip_existing: bool = True
                      ) -> Iterator[tuple[str, str]]:
        """Yield (thread_id, run_id) for runs this client did NOT start.

        That is how a turn typed into the Studio browser box reaches the
        speaker. Polls busy threads rather than holding a socket, so a server
        restart costs one poll interval and never a wedged connection. A run
        that begins and ends inside one interval is missed by design — this is
        a dev convenience, not a delivery guarantee.

        `skip_existing` swallows the first poll: runs already in flight when the
        watcher starts belong to the previous session, not to anyone sitting
        here now. Without it a restart greets you with a stale answer — seen
        live 2026-09-05, when a run abandoned by barge-in finished after the
        bridge had been restarted and was spoken into an empty room.
        """
        warming = skip_existing
        while stop is None or not stop():
            try:
                threads = self._get_client().threads.search(status="busy", limit=10)
            except Exception as exc:
                self._last_error = str(exc)
                self._client = None
                time.sleep(max(poll_s, 2.0))
                continue

            for th in threads:
                tid = th.get("thread_id") if isinstance(th, dict) else getattr(th, "thread_id", None)
                if not tid:
                    continue
                try:
                    runs = self._get_client().runs.list(str(tid), limit=5)
                except Exception:
                    continue
                if self._is_own_thread(str(tid)):
                    continue
                for run in runs:
                    rid = str(run.get("run_id") if isinstance(run, dict)
                              else getattr(run, "run_id", "") or "")
                    status = str(run.get("status") if isinstance(run, dict)
                                 else getattr(run, "status", "") or "")
                    if not rid or rid in self._own_runs or rid in self._seen_runs:
                        continue
                    if status not in ("pending", "running"):
                        continue
                    self._remember_seen(rid)
                    if not warming:
                        yield str(tid), rid
            warming = False
            time.sleep(poll_s)

    def join_run(self, thread_id: str, run_id: str,
                 interrupted: Callable[[], bool] | None = None) -> Iterator[TurnEvent]:
        """Stream a run that is already in flight (see `iter_new_runs`)."""
        try:
            stream = self._get_client().runs.join_stream(
                thread_id, run_id, stream_mode=["messages", "values"])
        except Exception as exc:
            logger.warning("Could not join run %s: %s", run_id, exc)
            yield TurnError(f"could not join run {run_id} ({exc})")
            return
        yield from self._consume(stream, thread_id, interrupted, own=False)

    # Grace window: a finished run can still be listed as running for a moment
    # after our stream ends, so the thread stays "ours" briefly past the turn.
    OWN_THREAD_GRACE_S = 3.0

    def _is_own_thread(self, thread_id: str) -> bool:
        """True while a turn we drove could still surface on this thread.

        Pinned threads (`pin_thread`) are deliberately NOT excluded outside that
        window — sharing one conversation with the Studio UI is the whole point
        of pinning, and typed turns there must still be spoken.
        """
        if thread_id != self._thread_id:
            return False
        return bool(self._driving) or (time.time() - self._drove_at) < self.OWN_THREAD_GRACE_S

    def _remember_seen(self, run_id: str) -> None:
        if len(self._seen_runs) > 500:      # dev session, not a ledger
            self._seen_runs.clear()
        self._seen_runs.add(run_id)

    # ── Shared event loop (driving and watching parse identically) ──────────

    def _consume(self, stream, thread_id: str,
                 interrupted: Callable[[], bool] | None,
                 tracker: "_TextTracker | None" = None,
                 own: bool = True) -> Iterator[TurnEvent]:
        tracker = tracker or _TextTracker()
        run_id: str | None = None

        for part in stream:
            if interrupted is not None and interrupted():
                self._cancel(thread_id, run_id)
                return

            event = getattr(part, "event", "") or ""
            data = getattr(part, "data", None)

            if event.startswith("metadata"):
                if isinstance(data, dict) and data.get("run_id"):
                    run_id = str(data["run_id"])
                    if own:
                        self._own_runs.add(run_id)
                continue
            if event.startswith("error"):
                yield TurnError(self._error_text(data))
                return
            if event.startswith("values"):
                if isinstance(data, dict):
                    yield Values(data)
                continue
            if not event.startswith("messages"):
                continue

            for msg, meta in _iter_messages(data):
                node = str(meta.get("langgraph_node", ""))
                if node and node in self._cfg.skip_nodes:
                    continue
                if msg.get("type") in ("tool", "system", "human"):
                    continue
                body = _content_text(msg.get("content"))
                if not body:
                    continue
                new = tracker.delta(str(msg.get("id") or id(msg)), body)
                if new:
                    yield Token(new, node=node)

    def _cancel(self, thread_id: str, run_id: str | None) -> None:
        """Best-effort server-side cancel so an abandoned turn stops generating."""
        if not run_id:
            return
        try:
            self._get_client().runs.cancel(thread_id, run_id)
            logger.info("Cancelled Studio run %s (barge-in)", run_id)
        except Exception as exc:
            logger.debug("Studio run cancel failed: %s", exc)

    @staticmethod
    def _error_text(data) -> str:
        if isinstance(data, dict):
            return str(data.get("message") or data.get("error") or data)
        return str(data)

    # ── Introspection ───────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "url": self._cfg.url,
            "assistant_id": self._cfg.assistant_id,
            "thread_id": self._thread_id,
            "thread_mode": self._cfg.thread_mode,
            "turns": self._turns,
            "last_error": self._last_error,
        }
