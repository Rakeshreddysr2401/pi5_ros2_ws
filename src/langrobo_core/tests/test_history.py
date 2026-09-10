"""Tests for cache-aware history trimming (utils.history)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langrobo_core.utils.history import CAMERA_VIEW_MARKER, has_image, trim_history
from langrobo_core.utils.pose_stamp import view_label


def _frame(i=0):
    # The real label look() writes -- a pose stamp, not the word "current".
    # Building it through view_label() is what keeps this test honest if the
    # stamp's wording changes again: is_camera_frame() has to keep matching it.
    return HumanMessage(content=[
        {"type": "text", "text": view_label((1.0 + i, 0.5, 30.0 * i))},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,FRAME{i}"}},
    ])


def _turns(n, start=0):
    out = []
    for i in range(start, start + n):
        out += [HumanMessage(content=f"q{i}"), AIMessage(content=f"a{i}")]
    return out


def test_untrimmed_history_is_untouched():
    msgs = _turns(3)
    out, changed = trim_history(msgs, max_len=10)
    assert not changed
    assert out == msgs


def test_trim_cuts_at_human_boundary():
    msgs = _turns(10)  # 20 messages
    out, changed = trim_history(msgs, max_len=8)
    assert changed
    assert len(out) <= 8
    assert isinstance(out[0], HumanMessage)
    assert str(out[0].content).startswith("q")


def test_trim_never_starts_at_camera_frame():
    # Layout: ... AI(tool_call-ish) frame AI human AI ... — force the naive cut
    # point to land exactly on the frame message.
    msgs = _turns(3) + [
        HumanMessage(content="what do you see"),
        AIMessage(content="", tool_calls=[
            {"name": "look", "args": {}, "id": "l1", "type": "tool_call"}]),
        ToolMessage(content="Captured the current camera view.", name="look", tool_call_id="l1"),
        _frame(1),
        AIMessage(content="a mug"),
    ] + _turns(2, start=10)
    # naive cut index = len - 6 → lands on the frame; must advance to next real human
    out, changed = trim_history(msgs, max_len=6, keep_frames=2)
    assert changed
    assert isinstance(out[0], HumanMessage)
    assert not has_image(out[0])
    assert CAMERA_VIEW_MARKER not in str(out[0].content)


def test_frame_eviction_keeps_newest_two():
    msgs = []
    for i in range(4):
        msgs += [
            HumanMessage(content=f"look {i}"),
            _frame(i),
            AIMessage(content=f"scene {i}"),
        ]
    out, changed = trim_history(msgs, max_len=11, keep_frames=2)
    assert changed
    frames = [m for m in out if has_image(m)]
    assert len(frames) == 2
    # newest frames survive
    urls = [p["image_url"]["url"] for m in frames for p in m.content
            if isinstance(p, dict) and p.get("type") == "image_url"]
    assert urls == ["data:image/jpeg;base64,FRAME2", "data:image/jpeg;base64,FRAME3"]
    # evicted ones leave a tombstone that still reads as a past camera view
    tombstones = [m for m in out if isinstance(m, HumanMessage)
                  and isinstance(m.content, str) and "frame removed" in m.content]
    assert len(tombstones) >= 1
