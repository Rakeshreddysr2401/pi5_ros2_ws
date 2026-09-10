"""graph/build.py's vision-question backstop.

The bug this guards: with `navigate` sticky from an earlier move, a real
transcript (PERCEPTION_STATE.md, 2026-09-10) shows the Mac mini's 12B model
answering "what are you looking at" directly -- specific, confident prose,
from an agent with no camera tool and no image anywhere in its context.
local_agent was never entered, so no amount of stamping look()'s frame could
have helped: the question never reached the agent that owns vision.

CHAT_PROMPT and NAVIGATE_PROMPT both already say to hand over for this. These
tests are the enforcement half -- a deterministic check on top of an
instruction the model has been observed ignoring.
"""

from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core.graph.build import _vision_backstop, _loop_guarded


def _state(query: str, active_agent="navigate", agent_run_counts=None):
    return {
        "messages": [HumanMessage(content=query)],
        "active_agent": active_agent,
        "agent_run_counts": agent_run_counts or {},
    }


def _spoke(text="I am looking at the area around the chair."):
    return {"messages": [AIMessage(content=text)]}


# ── Fires on exactly the transcript's failure ───────────────────────────────

def test_catches_a_sticky_navigate_answering_what_are_you_looking_at():
    redirect = _vision_backstop("navigate", _state("what are you looking at"), _spoke())
    assert redirect is not None
    assert redirect.goto == "local_agent"
    assert redirect.update["active_agent"] == "local_agent"


def test_catches_chat_too():
    redirect = _vision_backstop("chat", _state("what do you see"), _spoke())
    assert redirect is not None and redirect.goto == "local_agent"


def test_catches_the_rephrased_form_from_the_same_transcript():
    query = "I mean you rotated moved tell me what are you looking at"
    redirect = _vision_backstop("navigate", _state(query), _spoke())
    assert redirect is not None


# ── Does not fire when it must not ──────────────────────────────────────────

def test_never_fires_on_local_agent_itself():
    """Its own prompt rule 7 covers this; the backstop must not re-trigger on
    local_agent's own reply, or a correct answer could loop."""
    redirect = _vision_backstop("local_agent", _state("what do you see"), _spoke())
    assert redirect is None


def test_does_not_fire_when_local_agent_already_ran_this_turn():
    """A real answer from local_agent, reached earlier in the same turn via a
    normal handover, must never be overridden."""
    state = _state("what do you see", agent_run_counts={"local_agent": 1})
    redirect = _vision_backstop("navigate", state, _spoke())
    assert redirect is None


def test_does_not_fire_on_a_tool_call():
    """A move, a search, a correctly-called send_telegram_photo -- none of
    these are "answering without looking"; they are the agent doing its job."""
    out = {"messages": [AIMessage(content="", tool_calls=[
        {"name": "move_robot", "args": {"command": "F:20"}, "id": "c1", "type": "tool_call"}])]}
    redirect = _vision_backstop("navigate", _state("what do you see"), out)
    assert redirect is None


def test_does_not_fire_on_an_ordinary_question():
    redirect = _vision_backstop("chat", _state("what time is it"), _spoke("It's 3pm."))
    assert redirect is None


def test_take_a_photo_is_not_a_vision_question():
    """chat legitimately owns send_telegram_photo, which grabs a genuinely
    fresh frame on its own (see that tool's docstring) -- this must not force
    a redundant local_agent hop for a request chat already handles correctly."""
    redirect = _vision_backstop(
        "chat", _state("please take a new fresh image and send it"),
        _spoke("I've sent a fresh photo to Rakesh."))
    assert redirect is None


def test_does_not_fire_on_empty_content():
    """A silent handover (content='') is normal chaining, not a spoken answer
    — must not be mistaken for one."""
    out = {"messages": [AIMessage(content="")]}
    redirect = _vision_backstop("navigate", _state("what do you see"), out)
    assert redirect is None


# ── The routing note ─────────────────────────────────────────────────────────

def test_the_note_names_the_agent_and_the_question():
    redirect = _vision_backstop("navigate", _state("what are you looking at"), _spoke())
    note = redirect.update["messages"][0]
    assert "navigate" in note.content
    assert "what are you looking at" in note.content
    assert "look()" in note.content


def test_the_wrong_reply_is_not_deleted():
    """message_utils' append-only invariant: the offending AIMessage is history,
    not something a later step may remove."""
    state = _state("what do you see")
    out = _spoke("I am looking at the chair.")
    redirect = _vision_backstop("navigate", state, out)
    # The backstop's job is to chain onward, not to touch `out["messages"]`.
    assert out["messages"][0].content == "I am looking at the chair."
    assert redirect.update["messages"] != out["messages"]  # a note, not a copy


# ── Wired into the loop-guarded wrapper ─────────────────────────────────────

def test_loop_guarded_wrapper_returns_the_redirect_not_the_wrong_answer():
    fake_node = lambda state: {"messages": [AIMessage(content="I am looking at the chair.")],
                                "active_agent": "navigate"}
    wrapped = _loop_guarded("navigate", fake_node)
    out = wrapped(_state("what are you looking at"))
    assert out.goto == "local_agent"
    assert "agent_run_counts" in out.update
    assert out.update["agent_run_counts"]["navigate"] == 1


def test_loop_guarded_wrapper_passes_through_a_normal_reply():
    fake_node = lambda state: {"messages": [AIMessage(content="It's 3pm.")],
                                "active_agent": "chat"}
    wrapped = _loop_guarded("chat", fake_node)
    out = wrapped(_state("what time is it", active_agent="chat"))
    assert isinstance(out, dict)
    assert out["messages"][0].content == "It's 3pm."
