"""Fix 1 (2026-10-29): "can you see X?" and "not that one" get a FRESH look.

The Telegram transcript, robot unmoved, last photo 34 minutes old:
  "Can you see my bag"            -> chat called ask_photos and answered from the
                                     34-minute-old photo ("one meter to your left")
  "Send a pic"                    -> a fresh photo went to the phone
  "Hey not that black copart bag" -> chat: "I don't see a black copart bag in my
                                     memory" -- it never looked.
Both go to local_agent now, which gets a new photo before it answers.
"""
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import langrobo_core.graph.build as gb
from langrobo_core.graph.build import _VISION_QUESTION, _corrects_a_sighting, _vision_backstop
from langrobo_core.utils import pose_stamp


def _call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c_{name}",
                                              "type": "tool_call"}])


def _turn(user, *steps):
    return [HumanMessage(content=user), *steps]


# ── "can you see ..." is a current-view question ─────────────────────────────

@pytest.mark.parametrize("text", [
    "Can you see my bag", "can you see the red bottle?", "Kino, do you see a chair",
    "can you still see it", "are you seeing anyone",
])
def test_present_tense_seeing_is_a_current_view_question(text):
    assert _VISION_QUESTION.search(text)


@pytest.mark.parametrize("text", [
    "do you see what I mean", "where is my bag", "send me a photo", "what time is it",
    "see you later", "I can see it now",
])
def test_not_a_current_view_question(text):
    assert not _VISION_QUESTION.search(text)


def test_chat_answering_can_you_see_from_old_photos_goes_to_local_agent():
    state = {"messages": [HumanMessage(content="Can you see my bag")], "agent_run_counts": {}}
    out = {"messages": [_call("ask_photos", question="where is the user's bag?")]}
    redirect = _vision_backstop("chat", state, out)
    assert redirect is not None and redirect.goto == "local_agent"


# ── a correction of a sighting ───────────────────────────────────────────────

def _history_then(correction, previous_tool="send_telegram_photo"):
    prev = _turn("Send a pic", _call(previous_tool, recipient="Rakesh", caption=""),
                 ToolMessage("Photo sent.", tool_call_id=f"c_{previous_tool}"),
                 AIMessage(content="I have sent a photo."))
    return {"messages": prev + [HumanMessage(content=correction)], "agent_run_counts": {}}


@pytest.mark.parametrize("text", [
    "Hey not that black copart bag", "no, the other one", "Not that one", "that's not my bag",
    "wrong bag", "nope", "Kino, no, the blue one", "it's not the bag I meant",
])
def test_a_correction_after_a_photo_is_caught(text):
    assert _corrects_a_sighting(_history_then(text))


@pytest.mark.parametrize("text", [
    # exactly as the brain hands it over (live test 2026-10-29: missed before the prefix fix)
    "[Time now: 11:03 PM, say: three minutes past eleven at night] [Robot now at x=1.77 y=0.19 "
    "heading=-111° — unmoved since the last camera view, taken 15 s ago] No, not that one, the other bed",
    "[Time now: 10:31 PM, say: twenty-nine minutes to eleven at night] [Robot now at x=0.00 y=-0.00 "
    "heading=0° — unmoved since the last camera view, taken 35 min ago] [Telegram from Rakesh] "
    "Hey not that black copart bag",
])
def test_a_correction_behind_the_context_prefix_is_caught(text):
    assert _corrects_a_sighting(_history_then(text))


def test_the_context_prefix_alone_is_not_a_correction():
    assert not _corrects_a_sighting(_history_then(
        "[Time now: 10:31 PM] [Robot now at x=0.00 y=0.00 heading=0°] [Telegram from Rakesh] send another"))


@pytest.mark.parametrize("tool", ["ask_photos", "look", "approach_described_object", "locate_object"])
def test_a_correction_after_any_sighting_tool(tool):
    assert _corrects_a_sighting(_history_then("not that one", previous_tool=tool))


def test_no_after_a_non_vision_turn_is_not_a_correction():
    prev = _turn("what time is it", AIMessage(content="It is ten past ten."))
    state = {"messages": prev + [HumanMessage(content="no, I meant in London")]}
    assert not _corrects_a_sighting(state)


def test_only_the_turn_just_before_counts():
    old = _turn("can you see my bag", _call("look"), AIMessage(content="Yes."))
    mid = _turn("what time is it", AIMessage(content="Ten past ten."))
    state = {"messages": old + mid + [HumanMessage(content="no, the other one")]}
    assert not _corrects_a_sighting(state)


def test_a_new_request_is_not_a_correction():
    assert not _corrects_a_sighting(_history_then("go near the black bag"))
    assert not _corrects_a_sighting(_history_then("send another one"))


def test_chat_answering_a_correction_itself_goes_to_local_agent():
    state = _history_then("Hey not that black copart bag")
    out = {"messages": [AIMessage(content="I don't see a black copart bag in my memory.")]}
    redirect = _vision_backstop("chat", state, out)
    assert redirect is not None and redirect.goto == "local_agent"
    assert "corrected" in redirect.update["messages"][0].content


# ── local_agent then gets a fresh photo first ────────────────────────────────

@pytest.fixture
def looks(monkeypatch):
    calls = []

    def fake_look(call_id):
        calls.append(call_id)
        return [ToolMessage("Captured the current camera view.", tool_call_id=call_id)]
    monkeypatch.setattr(gb, "_take_look", fake_look)
    yield calls
    pose_stamp.forget_view()


def _run_local(state):
    return gb._loop_guarded("local_agent", lambda s: {"messages": [AIMessage(content="ok")]})(state)


def test_a_correction_looks_again_even_with_a_recent_photo(looks):
    pose_stamp.record_view((0.0, 0.0, 0.0), when=time.time() - 5)     # 5 s old
    _run_local(_history_then("not that one"))
    assert len(looks) == 1


def test_can_you_see_with_a_stale_photo_looks_first(looks):
    pose_stamp.record_view((0.0, 0.0, 0.0), when=time.time() - 34 * 60)
    _run_local({"messages": [HumanMessage(content="Can you see my bag")], "agent_run_counts": {}})
    assert len(looks) == 1


def test_can_you_see_with_a_fresh_photo_does_not_look_again(looks):
    pose_stamp.record_view((0.0, 0.0, 0.0), when=time.time() - 10)
    _run_local({"messages": [HumanMessage(content="Can you see my bag")], "agent_run_counts": {}})
    assert looks == []
