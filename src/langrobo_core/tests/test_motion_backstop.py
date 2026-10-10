"""graph/build.py's movement-request backstop.

The bug this guards (2026-10-10, real transcript): the microphone merged
"Small water cool. Pizza is ready. I know you like to play. , follow me" into
one turn; it entered `chat`, which has no movement tool; chat saved a memory
and said "I am following you now". The robot never moved. The backstop: the
user's own words ask for movement, an agent that cannot move answered, so the
answer is discarded and navigate gets the turn.
"""

from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core.graph.build import _MOVERS, _loop_guarded, _motion_backstop

MERGED = "Small water cool. Pizza is ready. I know you like to play. , follow me"


def _state(query: str, active_agent="chat", agent_run_counts=None):
    return {
        "messages": [HumanMessage(content=query)],
        "active_agent": active_agent,
        "agent_run_counts": agent_run_counts or {},
    }


def _spoke(text="I am following you now, staying about one metre behind you."):
    return {"messages": [AIMessage(content=text)]}


def _called(name, args=None):
    return {"messages": [AIMessage(content="", tool_calls=[
        {"name": name, "args": args or {}, "id": "c1", "type": "tool_call"}])]}


def test_movers_come_from_the_registry():
    assert _MOVERS == ("navigate",)


# ── fires on the transcript, in both of its shapes ──────────────────────────

def test_chat_claiming_to_follow_goes_to_navigate():
    r = _motion_backstop("chat", _state(MERGED), _spoke())
    assert r is not None and r.goto == "navigate"
    assert r.update["active_agent"] == "navigate"
    assert "follow me" in r.update["messages"][0].content


def test_chat_saving_a_memory_instead_goes_to_navigate():
    r = _motion_backstop("chat", _state(MERGED), _called("memory", {"action": "remember"}))
    assert r is not None and r.goto == "navigate"


def test_local_agent_answering_a_drive_request():
    r = _motion_backstop("local_agent", _state("go near the red bottle"),
                         _spoke("I can see the red bottle on the floor."))
    assert r is not None and r.goto == "navigate"


def test_movement_phrasings():
    for q in ("come here", "Mitra, come to me", "follow me please", "turn left",
              "move forward a bit", "go to the kitchen", "go outside", "go back",
              "go through that door", "come closer", "drive to the table", "come back here"):
        assert _motion_backstop("chat", _state(q), _spoke("Okay.")) is not None, q


# ── leaves the right things alone ───────────────────────────────────────────

def test_a_handover_passes():
    assert _motion_backstop("chat", _state(MERGED), _called("handover", {"next_agent": "navigate"})) is None


def test_navigate_itself_is_never_redirected():
    assert _motion_backstop("navigate", _state("follow me"), _spoke()) is None


def test_after_navigate_ran_this_turn():
    # navigate already acted and handed the summary back: chat may speak
    st = _state("follow me", agent_run_counts={"navigate": 1, "chat": 1})
    assert _motion_backstop("chat", st, _spoke("I'm following you.")) is None


def test_system_turns_are_never_redirected():
    # a failed errand quoted back must not be re-driven
    q = ("[Time now: 6:39 PM] [SYSTEM] Navigation failed: blocked. You were asked "
         "to go to the kitchen -- NOT done.")
    assert _motion_backstop("chat", _state(q), _spoke("I could not get there.")) is None


def test_ordinary_talk_is_left_alone():
    for q in ("what time is it", "go to sleep", "tell me about the therapy stick",
              "I will come back later", "can you go online", "Friend.", "how far is it",
              "the turn signal is broken"):
        assert _motion_backstop("chat", _state(q), _spoke("Sure.")) is None, q


def test_an_empty_step_is_left_alone():
    assert _motion_backstop("chat", _state(MERGED), {"messages": [AIMessage(content="")]}) is None


# ── through the wrapper: the proposed reply is never kept ───────────────────

def test_wrapper_discards_the_false_claim():
    def fake_chat(state):
        return _spoke()
    out = _loop_guarded("chat", fake_chat)(_state(MERGED))
    assert getattr(out, "goto", None) == "navigate"
    texts = [m.content for m in out.update["messages"]]
    assert not any("I am following you now" in t for t in texts)
    assert out.update["agent_run_counts"]["chat"] == 1
