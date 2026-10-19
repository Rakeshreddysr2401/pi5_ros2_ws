"""The [silent] reply: the agent decides a sentence was not said to it.

2026-10-12 the "is this for me?" pre-filter in stt_node was turned off (it
dropped "Can you find the door?" whenever the Mac was busy); every sentence now
reaches the brain and the agent itself may answer [silent] (SPEECH_STYLE). The
marker must never be spoken, and the backstops must not turn a deliberate
silence into a forced handover -- room talk saying "turn left" is not an order.
"""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core.graph.build import _motion_backstop, _vision_backstop
from langrobo_core.prompts import PERSONA
from langrobo_core.utils.speech_stream import SILENT, SpeechStreamHandler, is_silent


def test_is_silent_forgives_case_space_and_a_full_stop():
    for s in ("[silent]", " [SILENT] ", "[silent].", "[Silent]!"):
        assert is_silent(s), s
    for s in ("", None, "silent", "[silent] but I heard you", ["[silent]"]):
        assert not is_silent(s), s


def test_the_marker_is_never_streamed_to_the_speaker():
    sent = []
    h = SpeechStreamHandler(sent.append)
    run = uuid4()
    h.on_chat_model_start(None, None, run_id=run)
    for t in ("[", "sil", "ent", "]"):
        h.on_llm_new_token(t, run_id=run)
    h.on_llm_end(None, run_id=run)
    assert sent == [] and h.chunks_sent == 0


def test_backstops_leave_a_deliberate_silence_alone():
    state = {"messages": [HumanMessage(content="and then she said turn left at the lights")],
             "active_agent": "chat", "agent_run_counts": {}}
    out = {"messages": [AIMessage(content=SILENT)]}
    assert _motion_backstop("chat", state, out) is None
    state = {"messages": [HumanMessage(content="what do you see on the table")],
             "active_agent": "chat", "agent_run_counts": {}}
    assert _vision_backstop("chat", state, out) is None


def test_every_agent_is_told_how_to_stay_silent():
    assert SILENT in PERSONA
