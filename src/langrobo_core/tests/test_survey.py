"""tools/survey.py — every photo becomes object memory, placed from the photo.

No robot, no LLM: the VLM's reply and the Jetson's grounding are faked. The
properties: objects are placed only with the PHOTO's depth and pose
(at_capture), surfaces are not objects, a photo from another odom origin is
dropped, and nothing runs while a search holds survey.paused().
"""
import threading
import time

import pytest

import langrobo_core.tools.survey as sv
from langrobo_core.services import object_memory as om
from langrobo_core.tools import _bridge

EPOCH = 1790489988.03
W, H = 896, 504


@pytest.fixture(autouse=True)
def memory_file(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "path", lambda: str(tmp_path / "object_memory.json"))


class _Bridge:
    def __init__(self, replies=None, epoch=EPOCH):
        self.replies, self.epoch, self.queries = list(replies or []), epoch, []

    def get_origin_epoch(self):
        return self.epoch

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None):
        self.queries.append((round(u), round(v), stamp, box))
        return self.replies.pop(0) if self.replies else {"ok": False, "reason": "snapshot_expired"}


def _grounded(x, y, at_capture=True):
    return {"ok": True, "at_capture": at_capture, "region": True, "depth_m": 1.2,
            "object": {"x": x, "y": y}}


def _rec(when=None, epoch=EPOCH):
    return {"frame": b"jpeg", "stamp": (100, 5), "pose": (0.1, 0.2, 90.0),
            "source": "look", "when": time.time() if when is None else when, "epoch": epoch}


# ── parsing the VLM's list ──────────────────────────────────────────────────

def test_parse_boxes_to_pixels_and_skip_surfaces():
    reply = ('```json\n{"objects": [{"label": "White Chair", "box": [100, 200, 500, 400]},'
             ' {"label": "white wall", "box": [0, 0, 1000, 1000]},'
             ' {"label": "floor", "box": [800, 0, 1000, 1000]},'
             ' {"label": "black bag", "box": [600, 600, 900, 700]}]}\n```')
    objs = sv.parse_objects(reply, W, H)
    assert [label for label, _ in objs] == ["white chair", "black bag"]
    assert objs[0][1] == pytest.approx((200 * W / 1000, 100 * H / 1000, 400 * W / 1000, 500 * H / 1000))


@pytest.mark.parametrize("reply", ["", "no objects here", '{"objects": "none"}',
                                   '{"objects": [{"label": "cup", "box": [5, 5, 1, 1]}]}'])
def test_garbage_or_impossible_boxes_give_nothing(reply):
    assert sv.parse_objects(reply, W, H) == []


# ── placing what it saw ─────────────────────────────────────────────────────

def test_objects_are_placed_from_the_photo_and_remembered(monkeypatch):
    bridge = _Bridge([_grounded(1.5, 0.4), _grounded(-0.8, 2.0)])
    monkeypatch.setattr(_bridge, "_instance", bridge)
    monkeypatch.setattr(sv, "list_objects", lambda f: [
        ("white chair", (100, 50, 300, 250)), ("black bag", (500, 300, 600, 400))])
    rec = _rec(when=time.time() - 30)
    assert sv.survey_photo(rec) == 2
    # grounded at the PHOTO's stamp, on the VLM's box
    assert [q[2] for q in bridge.queries] == [(100, 5), (100, 5)]
    assert bridge.queries[0][3] == (100, 50, 300, 250)
    (chair,) = om.recall("white chair", EPOCH)
    assert (chair["x"], chair["y"]) == (1.5, 0.4)
    assert chair["seen_from"] == [0.1, 0.2, 90.0]            # where the photo was taken
    assert chair["seen_at"] == pytest.approx(rec["when"])     # when, not when surveyed
    assert chair["source"] == "survey:look"


def test_grounding_not_from_the_photo_is_not_kept(monkeypatch):
    """Newest-depth grounding after the robot moved would be the wrong place."""
    monkeypatch.setattr(_bridge, "_instance", _Bridge([_grounded(1.0, 1.0, at_capture=False), {"ok": False}]))
    monkeypatch.setattr(sv, "list_objects", lambda f: [("box", (1, 1, 9, 9)), ("cup", (1, 1, 9, 9))])
    assert sv.survey_photo(_rec()) == 0
    assert om.recall("", EPOCH) == []


def test_a_photo_from_another_odom_origin_is_dropped(monkeypatch):
    monkeypatch.setattr(_bridge, "_instance", _Bridge([_grounded(1.0, 1.0)], epoch=EPOCH + 5))
    called = []
    monkeypatch.setattr(sv, "list_objects", lambda f: called.append(1) or [])
    assert sv.survey_photo(_rec()) == 0 and called == []


def test_a_photo_without_a_camera_stamp_is_not_queued():
    assert sv.submit(b"jpeg", None, (0, 0, 0), "look") is False


# ── staying out of the way ──────────────────────────────────────────────────

def test_nothing_is_surveyed_while_paused_or_busy(monkeypatch):
    done = threading.Event()
    monkeypatch.setattr(sv, "survey_photo", lambda rec: done.set())
    busy = {"on": True}
    sv.set_busy_probe(lambda: busy["on"])
    try:
        with sv.paused():
            sv.submit(b"jpeg", (1, 2), (0, 0, 0), "search")
            assert not done.wait(1.5), "surveyed during a search"
        assert not done.wait(1.0), "surveyed during a turn"
        busy["on"] = False
        assert done.wait(3.0), "never surveyed once idle"
    finally:
        sv.set_busy_probe(lambda: False)


def test_status_reports_counters_and_queue():
    s = sv.status()
    assert {"photos", "objects", "skipped", "errors", "queued", "paused", "worker_alive"} <= set(s)
    with sv.paused():
        assert sv.status()["paused"] is True
    assert sv.status()["paused"] is False


def test_vision_tool_slot_fits_the_server(monkeypatch):
    """--parallel 4: its own slot. Smaller: the survey is off (never an agent's
    slot), and locate/search fall back to local_agent's so they still work."""
    monkeypatch.setattr(sv, "VISION_TOOL_SLOT", 3)
    assert sv.fit_slot(4, 1) == 3
    assert sv.fit_slot(None, 1) == 3                  # probe failed: as configured
    assert sv.fit_slot(3, 1) is None                  # --parallel 3: no slot 3
    assert sv.submit(b"jpeg", (1, 2), (0, 0, 0), "look") is False
    assert sv.status()["enabled"] is False


def test_one_shot_vision_calls_never_use_the_agents_slot(monkeypatch):
    """The vision conversation's cache (its photos) lives in local_agent's
    slot; a search view or survey there would overwrite it."""
    from langrobo_core.services import llm
    from langrobo_core.registry import SLOTS
    llm.configure("llamacpp", "m", "http://127.0.0.1:1", "none", 100,
                  {n: {"slot": s} for n, s in SLOTS.items()})
    monkeypatch.setattr(sv, "VISION_TOOL_SLOT", 3)
    assert sv.vision_tool_llm().extra_body["id_slot"] == 3
    assert llm.get_llm("local_agent").extra_body["id_slot"] == SLOTS["local_agent"]
    assert 3 not in SLOTS.values()
