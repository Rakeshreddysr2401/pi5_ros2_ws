"""tools/audio.py: what each request sends, what the user hears back, who may."""
import pytest

from langrobo_core.tools import _bridge
from langrobo_core.tools import audio as A

OWNER = {"sender_role": "owner"}
GUEST = {"sender_role": "guest", "sender_name": "Ravi"}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def audio(cmd, timeout=10.0):
        calls.append(("audio", cmd, timeout))
        return {"ok": True, "msg": "volume 70%"}

    def music(cmd, timeout=25.0):
        calls.append(("music", cmd, timeout))
        return {"ok": True, "msg": "playing Kesariya"}

    monkeypatch.setattr(_bridge.get(), "audio_request", audio)
    monkeypatch.setattr(_bridge.get(), "music_request", music)
    return calls


def run(tool, **args):
    return tool.func(**args)


def test_volume_requests(sent):
    assert run(A.speaker_volume, change=10, state=OWNER) == "volume 70%"
    run(A.speaker_volume, level=40, state=OWNER)
    run(A.speaker_volume, mute=True, state=OWNER)
    run(A.speaker_volume, state=OWNER)                       # read it
    cmds = [c[1] for c in sent]
    assert cmds == [{"op": "volume", "change": 10}, {"op": "volume", "set": 40},
                    {"op": "mute", "on": True}, {"op": "volume", "change": 0}]


def test_bluetooth_requests_and_timeouts(sent):
    run(A.bluetooth_device, state=OWNER)
    run(A.bluetooth_device, action="connect", name=" buds ", state=OWNER)
    run(A.bluetooth_device, action="pair", state=OWNER)
    assert [c[1] for c in sent] == [{"op": "bt", "action": "list", "name": ""},
                                    {"op": "bt", "action": "connect", "name": "buds"},
                                    {"op": "bt", "action": "pair", "name": ""}]
    assert sent[2][2] >= 45                                   # pairing scans 15 s, then pairs


def test_connect_without_a_name_asks(sent):
    assert "which device" in run(A.bluetooth_device, action="connect", state=OWNER).lower()
    assert not sent


def test_music_requests(sent):
    assert run(A.music, action="play", query="Kesariya", state=OWNER) == "playing Kesariya"
    run(A.music, action="stop", state=OWNER)
    assert [c[1] for c in sent] == [{"op": "play", "query": "Kesariya"}, {"op": "stop"}]
    assert "what to play" in run(A.music, action="play", query=" ", state=OWNER).lower()


def test_guest_refused_but_may_ask_status(sent):
    assert "Permission denied" in run(A.speaker_volume, change=10, state=GUEST)
    assert "Permission denied" in run(A.music, action="play", query="x", state=GUEST)
    assert "Permission denied" in run(A.bluetooth_device, action="connect", name="buds", state=GUEST)
    run(A.music, action="status", state=GUEST)
    run(A.bluetooth_device, state=GUEST)
    assert [c[1]["op"] for c in sent] == ["status", "bt"]


def test_voice_turn_without_identity_acts_as_owner(sent):
    assert run(A.speaker_volume, change=-10, state={}) == "volume 70%"


def test_no_reply_and_refusals_are_spoken_honestly(monkeypatch):
    monkeypatch.setattr(_bridge.get(), "audio_request", lambda cmd, timeout=10.0: {})
    assert "did not answer" in run(A.speaker_volume, change=10, state=OWNER)
    monkeypatch.setattr(_bridge.get(), "audio_request",
                        lambda cmd, timeout=10.0: {"ok": False, "msg": "no speaker is connected right now"})
    assert run(A.speaker_volume, change=10, state=OWNER) == \
        "Could not do it: no speaker is connected right now"


def test_tools_are_on_chat_only():
    from langrobo_core.tools import CHAT_TOOLS, LOCAL_AGENT_TOOLS, NAVIGATE_TOOLS
    names = {t.name for t in A.AUDIO_TOOLS}
    assert names <= {t.name for t in CHAT_TOOLS}
    assert not names & {t.name for t in LOCAL_AGENT_TOOLS + NAVIGATE_TOOLS}
