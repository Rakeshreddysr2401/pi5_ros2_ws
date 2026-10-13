"""utils/tool_speech.py + SpeechStreamHandler.say: speak first, act, and do
not say it twice (2026-10-12: first word 13.5 s after "find the helmet")."""

from uuid import uuid4

from langrobo_core.utils import tool_speech as ts
from langrobo_core.utils.speech_stream import SpeechStreamHandler


def test_lines_for_the_real_results_of_that_evening():
    assert ts.on_call("approach_described_object", {"description": "thy helmet"}) == \
        "Looking for the thy helmet."          # the recogniser's words, not ours to fix
    assert ts.on_call("approach_described_object", {"description": "my bag"}) == "Looking for the bag."
    assert ts.on_result("approach_described_object",
                        "It's still where I saw it. I can see the my bag — about 0.4 m away. "
                        "On my way; I'll say when I'm there. The robot has MOVED, so ...") == \
        "I see the bag, about 0.4 metres away. On my way."
    assert ts.on_result("move_robot", "Movement done: L:90. Measured: L:90 (turned +89 deg, "
                        "slid 14 cm). The robot has MOVED") == "Turned left 90 degrees."
    assert ts.on_result("move_robot", "Movement done: F:30. Measured: F:30 (moved 29 cm).") == \
        "Moved forward 30 centimetres."
    assert ts.on_result("move_robot", "Movement done: F:60, L:90, F:30. Measured: ...") == "Done."
    assert ts.on_result("follow_person", "Coming to you now: I'll stop about a metre away. "
                        "A system message will say when I'm there.") == "Coming to you now."


def test_failures_are_left_to_the_model():
    assert ts.on_result("move_robot", "Stopped before L:90: something in the way") is None
    assert ts.on_result("approach_described_object",
                        "I turned a full circle and looked carefully, but I couldn't spot it") is None
    assert ts.on_result("follow_person", "I can't see anyone to follow.") is None
    assert ts.on_call("move_robot", {"command": "L:90"}) is None     # quick: no preamble
    assert ts.on_result("handover", '{"next_agent": "navigate"}') is None


def _run(h, *tokens):
    run = uuid4()
    h.on_chat_model_start(None, None, run_id=run)
    for t in tokens:
        h.on_llm_new_token(t, run_id=run)
    h.on_llm_end(None, run_id=run)


def test_the_rephrasing_after_a_said_result_is_not_spoken():
    sent = []
    h = SpeechStreamHandler(sent.append)
    h.say("Looking for the bag.")
    h.say("I see the bag, about 0.4 metres away. On my way.", mute_next_run=True)
    _run(h, "I have found your bag ", "and am moving toward it now.")
    assert sent == ["Looking for the bag.", "I see the bag, about 0.4 metres away. On my way."]
    # agent_node then has nothing left to say but the end marker
    assert h.unspoken("I have found your bag and am moving toward it now.") == ""


def test_only_ONE_run_is_muted_so_a_second_step_is_still_heard():
    # "turn left and tell me what you see": the run after the turn hands over,
    # and local_agent's answer must be spoken
    sent = []
    h = SpeechStreamHandler(sent.append)
    h.say("Turned left 90 degrees.", mute_next_run=True)
    _run(h)                                     # navigate: a handover call, no text
    _run(h, "I can see a door and a chair.")
    assert sent == ["Turned left 90 degrees.", "I can see a door and a chair."]
