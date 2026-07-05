"""Telegram channel — outbound client + inbound long-poll. Pure zone (no rclpy).

Outbound: send_message / send_photo to allowlisted household members, driven
by the tools in tools/telegram.py. Inbound: a getUpdates long-poll daemon
(start_polling) that turns allowlisted members' messages into TelegramInbound
events for agent_node's turn queue — long polling needs no public IP, so the
Pi5 stays behind home NAT.

Design constraints this file honors (same contract as services/memory.py):
  - Missing token / empty allowlist → service reports itself unconfigured,
    tools answer "not set up", polling never starts, nothing crashes.
  - Never wedge a turn: sends run with tight timeouts and a single attempt;
    any failure returns an error string for the tool to speak honestly.
  - The bot never talks to strangers: non-allowlisted senders are ignored
    (logged once per chat_id, never answered).
  - Exactly-once across restarts: the getUpdates offset is persisted, so a
    brain restart neither replays nor drops messages.
  - Privacy: message contents are never logged — audit lines (in the tools)
    carry sender/recipient/outcome only. The bot token is never logged.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from . import metrics
from .config import TelegramConfig, TelegramMember

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"
_TEXT_LIMIT = 4096          # Telegram sendMessage hard cap
_CAPTION_LIMIT = 1024       # Telegram sendPhoto caption hard cap
_POLL_TIMEOUT_S = 50        # getUpdates long-poll window
_BACKOFF_MAX_S = 60
_RATE_LIMIT_N = 10          # max messages per sender…
_RATE_LIMIT_WINDOW_S = 60   # …per this window (protects the single turn queue)
_DEFAULT_OFFSET_PATH = "~/.langrobo/telegram_offset"
_DEFAULT_DEFERRED_PATH = "~/.langrobo/telegram_deferred.json"


_PHOTO_MAX_BYTES = 2_000_000   # inbound photo cap — it enters the LLM context


@dataclass(frozen=True)
class TelegramInbound:
    """One allowlisted inbound message, ready to become a turn."""
    chat_id: int
    name: str
    role: str
    text: str
    photo: bytes | None = None   # JPEG — enters the turn as an image block


class TelegramService:
    """Thin, thread-safe wrapper over the Bot API. Safe to construct always."""

    def __init__(self, cfg: TelegramConfig, transport=None,
                 offset_path: str = _DEFAULT_OFFSET_PATH):
        self._cfg = cfg
        self._transport = transport      # tests inject httpx.MockTransport
        self._client = None              # lazy — created on first send
        self._lock = threading.Lock()
        self._last_send_ts: float | None = None
        self._last_error: str | None = None
        # Inbound (start_polling)
        self._offset_path = os.path.expanduser(offset_path)
        self._deferred_path = os.path.expanduser(_DEFAULT_DEFERRED_PATH)
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._last_update_ts: float | None = None
        self._recent: dict[int, deque] = {}     # chat_id → recent message times
        self._warned_strangers: set[int] = set()

    # ── Directory ──────────────────────────────────────────────────────────

    def configured(self) -> bool:
        return self._cfg.configured

    def member_names(self) -> list[str]:
        return [m.name for m in self._cfg.members]

    def members_with_role(self, role: str) -> list[TelegramMember]:
        return [m for m in self._cfg.members if m.role == role]

    def member_by_name(self, name: str) -> TelegramMember | None:
        """Resolve a recipient the way the model refers to people: exact
        case-insensitive first, then an unambiguous prefix ('mom' → 'Mom')."""
        want = name.strip().casefold()
        if not want:
            return None
        for m in self._cfg.members:
            if m.name.casefold() == want:
                return m
        prefixed = [m for m in self._cfg.members if m.name.casefold().startswith(want)]
        return prefixed[0] if len(prefixed) == 1 else None

    def member_by_chat_id(self, chat_id: int) -> TelegramMember | None:
        for m in self._cfg.members:
            if m.chat_id == chat_id:
                return m
        return None

    # ── Sending (None = delivered, str = honest error for the tool) ────────

    def send_message(self, chat_id: int, text: str) -> str | None:
        return self._post("sendMessage", data={
            "chat_id": chat_id, "text": text[:_TEXT_LIMIT]})

    def send_photo(self, chat_id: int, jpeg: bytes, caption: str = "") -> str | None:
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:_CAPTION_LIMIT]
        return self._post("sendPhoto", data=data,
                          files={"photo": ("photo.jpg", jpeg, "image/jpeg")})

    def send_typing(self, chat_id: int) -> None:
        """Best-effort 'typing…' indicator while a turn runs. Never fails a turn."""
        self._post("sendChatAction", data={"chat_id": chat_id, "action": "typing"})

    # ── Quiet hours (proactive pings only — direct replies never defer) ────

    def quiet_now(self, now_minutes: int | None = None) -> bool:
        if self._cfg.quiet is None:
            return False
        if now_minutes is None:
            lt = time.localtime()
            now_minutes = lt.tm_hour * 60 + lt.tm_min
        start, end = self._cfg.quiet
        if start < end:
            return start <= now_minutes < end
        return now_minutes >= start or now_minutes < end   # overnight wrap

    def defer(self, chat_id: int, text: str) -> None:
        """Persist a proactive ping until quiet hours end (survives restarts)."""
        import json
        with self._lock:
            deferred = self._load_deferred()
            deferred.append({"chat_id": chat_id, "text": text[:_TEXT_LIMIT]})
            try:
                os.makedirs(os.path.dirname(self._deferred_path), exist_ok=True)
                tmp = self._deferred_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(deferred, f, indent=1)
                os.replace(tmp, self._deferred_path)
            except OSError:
                logger.exception("failed to persist deferred telegram ping")
        metrics.inc("telegram_deferred_total")

    def flush_deferred(self) -> None:
        """Send queued pings once outside quiet hours. Called from the poll
        loop (~once per poll cycle) — failed sends stay queued for retry."""
        if self.quiet_now():
            return
        with self._lock:
            deferred = self._load_deferred()
        if not deferred:
            return
        kept = [d for d in deferred if self.send_message(d["chat_id"], d["text"])]
        if len(kept) != len(deferred):
            logger.info("Flushed %d deferred telegram ping(s)", len(deferred) - len(kept))
        import json
        with self._lock:
            # defer() only appends — anything past our snapshot arrived while
            # we were sending and must survive the rewrite.
            kept += self._load_deferred()[len(deferred):]
            try:
                tmp = self._deferred_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(kept, f, indent=1)
                os.replace(tmp, self._deferred_path)
            except OSError:
                logger.exception("failed to persist deferred telegram pings")

    def _load_deferred(self) -> list:
        import json
        try:
            with open(self._deferred_path) as f:
                return list(json.load(f))
        except (OSError, ValueError):
            return []

    # ── Inbound long-poll ───────────────────────────────────────────────────

    def start_polling(self, on_message) -> None:
        """Start the getUpdates daemon. `on_message(TelegramInbound)` is called
        on the poller thread for each allowlisted message — the callback must
        only enqueue (agent_node's queues are thread-safe) and return fast."""
        if not self.configured():
            logger.info("Telegram polling not started — channel unconfigured")
            return
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(on_message,), daemon=True,
            name="telegram_poller")
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop.set()

    def _poll_loop(self, on_message) -> None:
        import httpx
        client = httpx.Client(
            base_url=f"{_API}/bot{self._cfg.token}",
            # Read timeout must outlive the server-held long poll.
            timeout=httpx.Timeout(_POLL_TIMEOUT_S + 15, connect=10.0),
            transport=self._transport,
        )
        offset = self._load_offset()
        backoff = 1
        logger.info("Telegram polling started (offset=%d)", offset)
        while not self._stop.is_set():
            try:
                resp = client.post("/getUpdates", json={
                    "offset": offset, "timeout": _POLL_TIMEOUT_S,
                    "allowed_updates": ["message"]})
                body = resp.json()
                if not body.get("ok"):
                    raise RuntimeError(f"Telegram API: {body.get('description', resp.status_code)}")
                backoff = 1
                self._last_update_ts = time.time()
                for update in body.get("result", []):
                    offset = max(offset, update["update_id"] + 1)
                    self._handle_update(update, on_message)
                if body.get("result"):
                    self._save_offset(offset)
                # Piggyback on the poll cadence (≤ ~1/min): release any pings
                # that were held during quiet hours.
                self.flush_deferred()
            except Exception as e:
                self._last_error = f"{type(e).__name__}: {e}"
                metrics.inc("telegram_poll_errors_total")
                logger.warning("telegram poll failed (retry in %ds) — %s",
                               backoff, self._last_error)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

    def _handle_update(self, update: dict, on_message) -> None:
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
        member = self.member_by_chat_id(chat_id)
        if member is None:
            # Anyone can find a bot; only the household gets a listener.
            if chat_id not in self._warned_strangers:
                self._warned_strangers.add(chat_id)
                logger.warning("Ignoring non-allowlisted Telegram chat_id %s", chat_id)
            metrics.inc("telegram_strangers_ignored_total")
            return
        if not self._allow_rate(chat_id):
            metrics.inc("telegram_rate_limited_total")
            logger.warning("Rate-limiting Telegram messages from %s", member.name)
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        photo = None
        if msg.get("photo"):
            photo = self._fetch_photo(msg["photo"])
            if photo is None:
                self.send_message(chat_id, "I couldn't download that photo — "
                                           "could you send it again?")
                if not text:
                    return
        if not text and photo is None:
            # Voice notes / documents / stickers — answer honestly rather than
            # silently swallowing the message.
            self.send_message(chat_id, "I can only read text and photos for now.")
            return
        metrics.inc("telegram_inbound_total")
        on_message(TelegramInbound(chat_id=chat_id, name=member.name,
                                   role=member.role, text=text[:_TEXT_LIMIT],
                                   photo=photo))

    def _fetch_photo(self, sizes: list) -> bytes | None:
        """Download the best PhotoSize: the largest variant under the byte cap
        (Telegram orders them small → large). Runs on the poller thread — off
        the turn path."""
        candidates = [p for p in sizes
                      if (p.get("file_size") or 0) <= _PHOTO_MAX_BYTES] or sizes[:1]
        file_id = candidates[-1].get("file_id")
        if not file_id:
            return None
        try:
            import httpx
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(
                        base_url=f"{_API}/bot{self._cfg.token}",
                        timeout=httpx.Timeout(15.0, connect=5.0),
                        transport=self._transport,
                    )
                meta = self._client.post("/getFile", data={"file_id": file_id}).json()
                if not meta.get("ok"):
                    raise RuntimeError(f"getFile: {meta.get('description')}")
                path = meta["result"]["file_path"]
                # File downloads live under /file/bot<token>/ — absolute URL
                # overrides the client's base_url.
                resp = self._client.get(f"{_API}/file/bot{self._cfg.token}/{path}")
                resp.raise_for_status()
            if len(resp.content) > _PHOTO_MAX_BYTES:
                return None
            metrics.inc("telegram_photos_received_total")
            return resp.content
        except Exception as e:
            logger.warning("telegram photo download failed — %s: %s",
                           type(e).__name__, e)
            return None

    def _allow_rate(self, chat_id: int) -> bool:
        now = time.time()
        recent = self._recent.setdefault(chat_id, deque())
        while recent and now - recent[0] > _RATE_LIMIT_WINDOW_S:
            recent.popleft()
        if len(recent) >= _RATE_LIMIT_N:
            return False
        recent.append(now)
        return True

    def _load_offset(self) -> int:
        try:
            with open(self._offset_path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return 0

    def _save_offset(self, offset: int) -> None:
        # Write-then-rename, same as the reminder store — a crash mid-write
        # must not corrupt the offset (that would replay or drop messages).
        try:
            os.makedirs(os.path.dirname(self._offset_path), exist_ok=True)
            tmp = self._offset_path + ".tmp"
            with open(tmp, "w") as f:
                f.write(str(offset))
            os.replace(tmp, self._offset_path)
        except OSError:
            logger.exception("failed to persist telegram offset")

    # ── Health surface ──────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "configured": self.configured(),
            "members": len(self._cfg.members),
            "polling": bool(self._poll_thread and self._poll_thread.is_alive()),
            "last_poll_age_s": round(time.time() - self._last_update_ts, 1)
                               if self._last_update_ts else None,
            "last_send_age_s": round(time.time() - self._last_send_ts, 1)
                               if self._last_send_ts else None,
            "last_error": self._last_error,
        }

    # ── Internals ───────────────────────────────────────────────────────────

    def _post(self, method: str, data: dict, files: dict | None = None) -> str | None:
        if not self.configured():
            return "Telegram is not configured on this robot."
        try:
            import httpx
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(
                        base_url=f"{_API}/bot{self._cfg.token}",
                        timeout=httpx.Timeout(15.0, connect=5.0),
                        transport=self._transport,
                    )
                resp = self._client.post(f"/{method}", data=data, files=files)
            body = resp.json()
            if not body.get("ok"):
                raise RuntimeError(f"Telegram API: {body.get('description', resp.status_code)}")
        except Exception as e:
            # Includes network errors, non-JSON bodies, API refusals. One
            # attempt only — the tool reports honestly instead of stalling
            # the turn on retries.
            self._last_error = f"{type(e).__name__}: {e}"
            metrics.inc("telegram_send_errors_total")
            logger.warning("telegram %s failed — %s", method, self._last_error)
            return f"Sending over Telegram failed ({type(e).__name__})."
        self._last_send_ts = time.time()
        self._last_error = None
        metrics.inc("telegram_sends_total")
        return None


# Module-level singleton — same pattern as services.memory: the entry point
# calls init() once; tools and agent_node share the instance.
_instance: TelegramService | None = None


def init(cfg: TelegramConfig) -> TelegramService:
    global _instance
    if _instance is None:
        _instance = TelegramService(cfg)
        if cfg.configured:
            logger.info("Telegram channel ready (%d allowlisted members)", len(cfg.members))
    return _instance


def get() -> TelegramService | None:
    """The active Telegram service, or None if init() was never called."""
    return _instance
