"""Tests for the per-agent message projection (utils.message_utils).

The property that matters most is CACHE APPEND-ONLY-NESS: for any log L and
suffix S, project(L) must be a prefix of project(L + S). If it isn't, an
agent's llama.cpp prompt diverges mid-history between turns and the server
re-prefills everything after the divergence point (including cached camera
frames on local_agent's slot).
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from langrobo_core.utils.message_utils import prepare_messages_for_agent


def _ho_call(cid="c1"):
    return {"name": "handover", "args": {"next_agent": "chat"}, "id": cid, "type": "tool_call"}


def _image_msg(text="[Current camera view]"):
    return HumanMessage(content=[
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ])


def _dump(msgs):
    return [(type(m).__name__, str(m.content)) for m in msgs]


def _turn(i):
    """One plain user/assistant exchange."""
    return [HumanMessage(content=f"question {i}"), AIMessage(content=f"answer {i}")]


def _handover_turn(i):
    """A turn that routes through handover: silent AI handover + tool msg + routing note."""
    return [
        HumanMessage(content=f"do thing {i}"),
        AIMessage(content="", tool_calls=[_ho_call(f"h{i}")]),
        ToolMessage(content='{"next_agent": "navigate"}', name="handover", tool_call_id=f"h{i}"),
        SystemMessage(content=f"[Routing note] Control passed to 'navigate' ({i})"),
        AIMessage(content=f"moved {i}"),
    ]


def test_projection_is_append_only_across_handovers():
    log = []
    prev = None
    # Grow the log through mixed turns — including multiple handovers, which
    # historically broke append-only-ness (only the most recent SystemMessage
    # was kept, so each new routing note deleted the previous one mid-history).
    for i, turn in enumerate([_turn(0), _handover_turn(1), _turn(2),
                              _handover_turn(3), [_image_msg(), AIMessage(content="a cat")]]):
        log = log + turn
        cur = _dump(prepare_messages_for_agent(log))
        if prev is not None:
            assert cur[:len(prev)] == prev, f"projection rewrote history at step {i}"
        prev = cur


def test_projection_append_only_with_images_kept():
    log = _turn(0) + [_image_msg(), AIMessage(content="I see a mug")]
    p1 = _dump(prepare_messages_for_agent(log, keep_images=True))
    log2 = log + _handover_turn(1) + _turn(2)
    p2 = _dump(prepare_messages_for_agent(log2, keep_images=True))
    assert p2[:len(p1)] == p1, "image-keeping projection rewrote history"


def test_all_system_messages_kept():
    log = [
        SystemMessage(content="[Routing note] one"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        SystemMessage(content="[Routing note] two"),
    ]
    out = prepare_messages_for_agent(log)
    notes = [m for m in out if isinstance(m, SystemMessage)]
    assert [m.content for m in notes] == ["[Routing note] one", "[Routing note] two"]


def test_handover_plumbing_stripped():
    log = _handover_turn(1)
    out = prepare_messages_for_agent(log)
    assert not any(isinstance(m, ToolMessage) for m in out)
    assert not any(getattr(m, "tool_calls", None) for m in out)
    # the silent handover AIMessage disappears entirely
    assert [str(m.content) for m in out if isinstance(m, AIMessage)] == ["moved 1"]


def test_spoken_handover_keeps_text_once():
    log = [
        HumanMessage(content="go and check"),
        AIMessage(content="On my way.", tool_calls=[_ho_call()]),
        ToolMessage(content='{"next_agent": "navigate"}', name="handover", tool_call_id="c1"),
        SystemMessage(content="[Routing note] to navigate"),
    ]
    out = prepare_messages_for_agent(log)
    spoken = [m for m in out if isinstance(m, AIMessage) and "On my way." in str(m.content)]
    assert len(spoken) == 1
    assert not spoken[0].tool_calls


def test_images_stripped_for_text_agents_only():
    log = [_image_msg(), AIMessage(content="a cat")]
    text_view = prepare_messages_for_agent(log)
    assert text_view[0].content == "[Current camera view]"
    vision_view = prepare_messages_for_agent(log, keep_images=True)
    assert any(p.get("type") == "image_url" for p in vision_view[0].content)
