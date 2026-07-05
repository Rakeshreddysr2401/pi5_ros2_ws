"""Channel-confirmation gate (tools/_relay_confirm.py) — D10 enforced in code.

A bare "tell Mom X" must not send anywhere until the user picks a channel;
the gate, not the prompt, guarantees it. Confirmation cannot be minted in
the same turn as the request — only a NEW user turn unlocks a pending ask.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core.tools import _relay_confirm


@pytest.fixture(autouse=True)
def _clean_gate():
    _relay_confirm.reset()
    yield
    _relay_confirm.reset()


def _state(*texts, channel="voice"):
    return {"channel": channel,
            "messages": [HumanMessage(content=t) for t in texts]}


def _check(state, kind="telegram"):
    return _relay_confirm.check(state, kind, ask_hint="phone or aloud?")


def test_bare_relay_is_refused_until_user_answers():
    state = _state("tell mom dinner is ready")
    refusal = _check(state)
    assert refusal and "Ask them ONE short question" in refusal
    # Retry within the SAME turn (no new user message) → still refused;
    # the model cannot mint its own confirmation.
    assert _check(state) is not None
    # The user answers (a new human turn exists) → whichever tool the model
    # now picks goes through.
    state["messages"].append(HumanMessage(content="the second one"))
    assert _check(state) is None


def test_explicit_channel_words_skip_the_ask():
    assert _check(_state("text mom that dinner is ready")) is None
    assert _check(_state("tell mom on telegram I'll be late")) is None
    assert _check(_state("tell mom out loud that food is ready"),
                  kind="announce") is None
    # Explicit for ONE channel doesn't unlock the OTHER.
    assert _check(_state("say it aloud to everyone")) is not None


def test_system_turns_and_errand_forwards_bypass():
    assert _check(_state("[SYSTEM] Reminder due — tell Mom …",
                         channel="system")) is None
    assert _check(_state("[Telegram from Mom] [This may answer the errand "
                         "Rakesh gave you …] I'll be back at 6",
                         channel="telegram")) is None


def test_pending_ask_expires():
    state = _state("tell mom dinner is ready")
    assert _check(state) is not None
    # The conversation moves on for more turns than the ask stays valid.
    for t in ("what's the weather", "play some jazz", "thanks",
              "set a timer for five minutes"):
        state["messages"].append(HumanMessage(content=t))
    assert _check(state) is not None   # stale — must ask again


def test_camera_frames_do_not_count_as_user_turns():
    state = _state("tell mom I said hi")
    assert _check(state) is not None
    # look() injects an image HumanMessage mid-turn — not a user answer.
    state["messages"].append(HumanMessage(content=[
        {"type": "text", "text": "[Current camera view]"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,x"}},
    ]))
    state["messages"].append(AIMessage(content=""))
    assert _check(state) is not None
