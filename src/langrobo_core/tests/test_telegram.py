"""Telegram channel tests: allowlist parsing, role→capability policy, service
degradation without a token, tool-level permission enforcement, and the send
path over a mocked httpx transport. No network, no robot."""

import time

import pytest

from langrobo_core.services import config as config_service
from langrobo_core.services import permissions
from langrobo_core.services import telegram as telegram_service
from langrobo_core.services.config import TelegramConfig, TelegramMember
from langrobo_core.services.telegram import TelegramService
from langrobo_core.tools import _bridge
from langrobo_core.tools.telegram import send_telegram_message, send_telegram_photo


# ── Allowlist parsing (config) ───────────────────────────────────────────────

def test_allowlist_parses_members(monkeypatch):
    monkeypatch.setenv("LANGROBO_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("LANGROBO_TELEGRAM_ALLOWLIST",
                       "111:Rakesh:owner, 222:Mom:family")
    tg = config_service.load_settings().telegram
    assert tg.configured
    assert tg.members == (TelegramMember(111, "Rakesh", "owner"),
                          TelegramMember(222, "Mom", "family"))


@pytest.mark.parametrize("raw", [
    "notanumber:Rakesh:owner",       # bad chat_id
    "111:Rakesh",                    # missing role
    "111:Rakesh:emperor",            # unknown role
    "111:Rakesh:owner,111:Mom:family",   # duplicate chat_id
    "111:Rakesh:owner,222:rakesh:family",  # duplicate name (case-insensitive)
])
def test_malformed_allowlist_fails_fast(monkeypatch, raw):
    monkeypatch.setenv("LANGROBO_TELEGRAM_ALLOWLIST", raw)
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()


def test_token_without_allowlist_stays_disabled(monkeypatch):
    monkeypatch.setenv("LANGROBO_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.delenv("LANGROBO_TELEGRAM_ALLOWLIST", raising=False)
    assert not config_service.load_settings().telegram.configured


# ── Role → capability policy ─────────────────────────────────────────────────

def test_capability_matrix():
    assert permissions.has_capability("owner", permissions.CAP_MOVE)
    assert permissions.has_capability("owner", permissions.CAP_PHOTO)
    assert permissions.has_capability("family", permissions.CAP_RELAY)
    assert not permissions.has_capability("family", permissions.CAP_PHOTO)
    assert not permissions.has_capability("family", permissions.CAP_MOVE)
    assert not permissions.has_capability("guest", permissions.CAP_RELAY)
    assert not permissions.has_capability("stranger", permissions.CAP_CHAT)


# ── Service directory + degradation ──────────────────────────────────────────

_CFG = TelegramConfig(
    token="123:abc",
    members=(TelegramMember(111, "Rakesh", "owner"),
             TelegramMember(222, "Mom", "family")),
    configured=True,
)


def test_member_resolution():
    svc = TelegramService(_CFG)
    assert svc.member_by_name("mom").chat_id == 222
    assert svc.member_by_name("Rak").chat_id == 111      # unambiguous prefix
    assert svc.member_by_name("nobody") is None
    assert svc.member_by_chat_id(222).name == "Mom"


def test_unconfigured_service_reports_honestly():
    svc = TelegramService(TelegramConfig())
    assert not svc.configured()
    assert "not configured" in svc.send_message(111, "hi")


# ── Send path over a mocked transport ────────────────────────────────────────

def _service_with_response(handler):
    import httpx
    return TelegramService(_CFG, transport=httpx.MockTransport(handler))


def test_send_message_ok_and_api_error():
    import httpx
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True, "result": {}})

    svc = _service_with_response(handler)
    assert svc.send_message(111, "hello") is None
    assert seen["path"].endswith("/sendMessage")
    assert svc.status()["last_error"] is None

    def refuse(request):
        return httpx.Response(400, json={"ok": False, "description": "chat not found"})

    svc = _service_with_response(refuse)
    err = svc.send_photo(111, b"\xff\xd8jpeg", "caption")
    assert err is not None
    assert svc.status()["last_error"] is not None


# ── Tools: permission gate + camera contract ─────────────────────────────────

class _FakeService:
    """Records sends; quacks like TelegramService for the tools."""

    def __init__(self):
        self.sent = []

    def configured(self):
        return True

    def member_names(self):
        return ["Rakesh", "Mom"]

    def member_by_name(self, name):
        return {"rakesh": TelegramMember(111, "Rakesh", "owner"),
                "mom": TelegramMember(222, "Mom", "family")}.get(name.casefold())

    def send_message(self, chat_id, text):
        self.sent.append(("message", chat_id))
        return None

    def send_photo(self, chat_id, jpeg, caption=""):
        self.sent.append(("photo", chat_id))
        return None


class _FakeBridge:
    def __init__(self, frame=b"\xff\xd8jpeg"):
        self._frame = frame

    def get_frame(self, max_age_s=10.0):
        return self._frame

    def frame_age(self):
        return None


@pytest.fixture
def fake_channel(monkeypatch):
    fake = _FakeService()
    monkeypatch.setattr(telegram_service, "_instance", fake)
    monkeypatch.setattr(_bridge, "_instance", _FakeBridge())
    return fake


def _explicit(text="text Mom saying hi"):
    """State whose user turn names the phone channel — passes the D10 gate."""
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)]}


def test_voice_turn_acts_as_owner(fake_channel):
    out = send_telegram_message.func(recipient="Mom", message="hi", state=_explicit())
    assert "delivered to Mom" in out
    out = send_telegram_photo.func(recipient="Rakesh", caption="", state={})
    assert "Photo sent to Rakesh" in out
    assert fake_channel.sent == [("message", 222), ("photo", 111)]


def test_family_sender_can_relay_but_not_photo(fake_channel):
    state = {"channel": "telegram", "sender_name": "Mom", "sender_role": "family", **_explicit("message Rakesh saying hi")}
    out = send_telegram_message.func(recipient="Rakesh", message="hi", state=state)
    assert "delivered to Rakesh" in out
    out = send_telegram_photo.func(recipient="Mom", caption="", state=state)
    assert "Permission denied" in out
    assert ("photo", 222) not in fake_channel.sent


def test_unknown_recipient_lists_members(fake_channel):
    out = send_telegram_message.func(recipient="Uncle", message="hi", state={})
    assert "Rakesh, Mom" in out


def test_photo_with_dead_camera_is_honest(fake_channel, monkeypatch):
    monkeypatch.setattr(_bridge, "_instance", _FakeBridge(frame=None))
    out = send_telegram_photo.func(recipient="Rakesh", caption="", state={})
    assert "cannot take a photo" in out
    assert fake_channel.sent == []


def test_tools_degrade_without_service(monkeypatch):
    monkeypatch.setattr(telegram_service, "_instance", None)
    out = send_telegram_message.func(recipient="Mom", message="hi", state={})
    assert "not set up" in out


# ── Inbound: update handling, allowlist, rate limit, offset ──────────────────

def _update(chat_id, text=None, caption=None, update_id=1):
    msg = {"chat": {"id": chat_id}}
    if text is not None:
        msg["text"] = text
    if caption is not None:
        msg["caption"] = caption
    return {"update_id": update_id, "message": msg}


@pytest.fixture
def inbound_svc(tmp_path):
    return TelegramService(_CFG, offset_path=str(tmp_path / "offset"))


def test_allowlisted_message_reaches_callback(inbound_svc):
    got = []
    inbound_svc._handle_update(_update(222, text="tell him food is in the fridge"),
                               got.append)
    assert len(got) == 1
    assert (got[0].name, got[0].role, got[0].chat_id) == ("Mom", "family", 222)
    assert got[0].text == "tell him food is in the fridge"


def test_stranger_is_silently_ignored(inbound_svc, monkeypatch):
    sent, got = [], []
    monkeypatch.setattr(inbound_svc, "send_message",
                        lambda cid, txt: sent.append(cid) or None)
    inbound_svc._handle_update(_update(999, text="open the door"), got.append)
    assert got == [] and sent == []       # no turn, no reply — strangers get nothing


def test_caption_counts_as_text(inbound_svc):
    got = []
    inbound_svc._handle_update(_update(111, caption="look at this"), got.append)
    assert got and got[0].text == "look at this"


def test_non_text_message_gets_honest_reply(inbound_svc, monkeypatch):
    sent, got = [], []
    monkeypatch.setattr(inbound_svc, "send_message",
                        lambda cid, txt: sent.append((cid, txt)) or None)
    inbound_svc._handle_update(_update(111), got.append)
    assert got == []
    assert sent and sent[0][0] == 111 and "text" in sent[0][1]


def test_rate_limit_drops_flood(inbound_svc):
    got = []
    for i in range(15):
        inbound_svc._handle_update(_update(111, text=f"msg {i}", update_id=i),
                                   got.append)
    assert len(got) == 10                 # bucket size — the rest dropped
    # …and the other member's budget is untouched.
    inbound_svc._handle_update(_update(222, text="hi"), got.append)
    assert len(got) == 11


def test_offset_roundtrip_survives_restart(tmp_path):
    path = str(tmp_path / "offset")
    svc = TelegramService(_CFG, offset_path=path)
    assert svc._load_offset() == 0        # first boot
    svc._save_offset(4242)
    assert TelegramService(_CFG, offset_path=path)._load_offset() == 4242


def test_polling_never_starts_unconfigured():
    svc = TelegramService(TelegramConfig())
    svc.start_polling(lambda i: pytest.fail("must not be called"))
    assert not svc.status()["polling"]


# ── Inbound photos ───────────────────────────────────────────────────────────

def _photo_update(chat_id, caption=None):
    msg = {"chat": {"id": chat_id},
           "photo": [{"file_id": "small", "file_size": 5_000},
                     {"file_id": "big", "file_size": 90_000}]}
    if caption:
        msg["caption"] = caption
    return {"update_id": 7, "message": msg}


def test_photo_downloaded_into_inbound(tmp_path):
    import httpx

    def handler(request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={
                "ok": True, "result": {"file_path": "photos/file_1.jpg"}})
        if "/file/bot" in str(request.url):
            return httpx.Response(200, content=b"\xff\xd8fakejpeg")
        return httpx.Response(200, json={"ok": True, "result": {}})

    svc = TelegramService(_CFG, transport=httpx.MockTransport(handler),
                          offset_path=str(tmp_path / "offset"))
    got = []
    svc._handle_update(_photo_update(111, caption="what is this?"), got.append)
    assert got and got[0].photo == b"\xff\xd8fakejpeg"
    assert got[0].text == "what is this?"


def test_photo_download_failure_is_honest(tmp_path):
    import httpx

    sent = []

    def handler(request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(400, json={"ok": False, "description": "gone"})
        if request.url.path.endswith("/sendMessage"):
            sent.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    svc = TelegramService(_CFG, transport=httpx.MockTransport(handler),
                          offset_path=str(tmp_path / "offset"))
    got = []
    svc._handle_update(_photo_update(111), got.append)   # photo only, no caption
    assert got == [] and len(sent) == 1                  # apology, no turn


# ── Quiet hours ──────────────────────────────────────────────────────────────

def test_quiet_hours_parse_and_validation(monkeypatch):
    monkeypatch.setenv("LANGROBO_QUIET_HOURS", "23:00-07:00")
    assert config_service.load_settings().telegram.quiet == (23 * 60, 7 * 60)
    monkeypatch.setenv("LANGROBO_QUIET_HOURS", "25:00-07:00")
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()
    monkeypatch.setenv("LANGROBO_QUIET_HOURS", "nonsense")
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()


def test_quiet_now_overnight_wrap():
    cfg = TelegramConfig(token="t", members=_CFG.members, configured=True,
                         quiet=(23 * 60, 7 * 60))
    svc = TelegramService(cfg)
    assert svc.quiet_now(now_minutes=23 * 60 + 30)   # 23:30 — quiet
    assert svc.quiet_now(now_minutes=3 * 60)         # 03:00 — quiet
    assert not svc.quiet_now(now_minutes=12 * 60)    # noon — not quiet
    assert not svc.quiet_now(now_minutes=7 * 60)     # 07:00 sharp — window ends
    assert TelegramService(_CFG).quiet_now() is False   # no window configured


def test_proactive_send_defers_during_quiet_hours(monkeypatch, fake_channel):
    monkeypatch.setattr(fake_channel, "quiet_now", lambda: True, raising=False)
    deferred = []
    monkeypatch.setattr(fake_channel, "defer",
                        lambda cid, txt: deferred.append(cid), raising=False)
    system_state = {"channel": "system"}
    out = send_telegram_message.func(recipient="Mom", message="reminder!",
                                     state=system_state)
    assert "quiet hours" in out and deferred == [222]
    assert fake_channel.sent == []                   # nothing sent now
    # A direct (voice/telegram channel) request still goes through.
    out = send_telegram_message.func(recipient="Mom", message="hi", state=_explicit())
    assert fake_channel.sent == [("message", 222)]


def test_deferred_flush_after_quiet_hours(tmp_path, monkeypatch):
    import httpx
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {}})

    svc = TelegramService(_CFG, transport=httpx.MockTransport(handler),
                          offset_path=str(tmp_path / "offset"))
    monkeypatch.setattr(svc, "_deferred_path", str(tmp_path / "deferred.json"))
    quiet = {"on": True}
    monkeypatch.setattr(svc, "quiet_now", lambda now_minutes=None: quiet["on"])
    svc.defer(111, "good morning ping")
    svc.flush_deferred()
    assert sent == []                                # still quiet — held
    quiet["on"] = False
    svc.flush_deferred()
    assert sent and sent[0].endswith("/sendMessage") # released
    svc.flush_deferred()
    assert len(sent) == 1                            # queue drained, no resend


# ── Errand ledger ────────────────────────────────────────────────────────────

def _doc_update(chat_id, file_name, caption=None, size=1000, update_id=1):
    msg = {"chat": {"id": chat_id},
           "document": {"file_id": "f1", "file_name": file_name,
                        "file_size": size}}
    if caption:
        msg["caption"] = caption
    return {"update_id": update_id, "message": msg}


