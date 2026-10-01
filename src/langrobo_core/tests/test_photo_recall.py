"""tools/photos.py (the photo log) and tools/photo_recall.py (ask_photos).

No robot, no LLM: the vision model's replies and the Jetson's grounding are
faked. The properties: the log is append-only (a known photo keeps its place,
old ones go DROP_BLOCK at a time) so the vision model's image prefix stays
cached; the request never carries an age; a photo is placed with ITS OWN
stamp, and distance/bearing come from the pose NOW; with its depth gone the
answer is a direction only, and only if the robot has not moved off.
"""
import io
import math
import time

import pytest
from PIL import Image

import langrobo_core.tools.photo_recall as ap
import langrobo_core.tools.photos as ph
import langrobo_core.tools.survey as sv
from langrobo_core.tools import _bridge

EPOCH = 1790489988.03
W, H = 896, 504


def _jpeg(shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), (shade, shade, shade)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def empty_log():
    ph.clear()
    yield
    ph.clear()


class _Bridge:
    def __init__(self, pose=(0.0, 0.0, 0.0), ground=None, epoch=EPOCH):
        self.pose, self.ground, self.epoch, self.queries = pose, ground, epoch, []

    def get_origin_epoch(self):
        return self.epoch

    def get_current_pose(self):
        return self.pose

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None):
        self.queries.append((round(u), round(v), stamp, box))
        return self.ground or {"ok": False, "reason": "snapshot_expired"}


class _Reply:
    def __init__(self, content):
        self.content = content


class _LLM:
    """Hands out canned replies and keeps every request it was sent."""
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def invoke(self, msgs, config=None):
        self.requests.append(msgs)
        return _Reply(self.replies.pop(0))


def _use(monkeypatch, bridge, llm):
    monkeypatch.setattr(_bridge, "_instance", bridge)
    monkeypatch.setattr(sv, "vision_tool_llm", lambda **kw: llm)


def _log(n, pose=(0.0, 0.0, 0.0), when=None, epoch=EPOCH, first=0):
    return [ph.record(_jpeg(10 * (first + i) + 5), (100 + first + i, 0), pose,
                      (when or time.time()) - (n - i) * 30, epoch, "search") for i in range(n)]


# ── the photo log ───────────────────────────────────────────────────────────

def test_photos_are_numbered_in_order_and_a_known_photo_keeps_its_place():
    a, b = _log(2)
    again = ph.record(_jpeg(5), (999, 0), (9.0, 9.0, 9.0), time.time(), EPOCH, "look")
    assert again is a                      # same image: same record, not moved, not renumbered
    assert [r["n"] for r in ph.log()] == [a["n"], b["n"]]


def test_old_photos_go_a_block_at_a_time():
    _log(ph.MAX_PHOTOS)
    first = ph.log()[0]["n"]
    ph.record(_jpeg(250), (1, 1), (0, 0, 0), time.time(), EPOCH, "look")
    kept = ph.log()
    assert len(kept) == ph.MAX_PHOTOS + 1 - ph.DROP_BLOCK
    assert kept[0]["n"] == first + ph.DROP_BLOCK          # oldest block gone, order kept


def test_photos_from_another_odom_origin_are_not_used():
    _log(2, epoch=1.0)
    _log(1, first=2)
    assert len(ph.log(EPOCH)) == 1


def test_no_stamp_is_not_logged():
    assert ph.record(_jpeg(1), None, (0, 0, 0), time.time(), EPOCH, "look") is None
    assert ph.log() == []


# ── the request: cacheable ──────────────────────────────────────────────────

def test_image_prefix_is_identical_across_questions_and_carries_no_age():
    log = _log(3)
    first = ap.photo_parts(log)
    time.sleep(0.01)
    assert ap.photo_parts(ph.log()) == first
    labels = [p["text"] for p in first if p["type"] == "text"]
    assert labels[0] == ap._ASK_INSTRUCTIONS            # instructions first: cached too
    assert labels[1:] == [f"Photo {r['n']}:" for r in log]


def test_what_follows_the_photos_stays_short():
    # Everything after the photo prefix must be rewound by the next question;
    # the Mac keeps its cache only for a rewind of <= ~450 tokens (docstring).
    tail = ap._QUESTION.format(question="where is the small blue box near the door?")
    box = ap._BOX_PROMPT.format(n=12, obj="small blue box")
    assert len(tail) + len(box) < 200 and ap._ANSWER_MAX_TOKENS <= 120


def test_second_question_resends_the_same_prefix(monkeypatch):
    _log(3)
    llm = _LLM('{"answer": "No.", "object": null, "photo": null, "box": null}',
               '{"answer": "No.", "object": null, "photo": null, "box": null}')
    _use(monkeypatch, _Bridge(), llm)
    ap.ask("is there a cat?")
    ap.ask("is there a dog?")
    one, two = (r[0].content for r in llm.requests)
    assert one[:-1] == two[:-1] and one[-1] != two[-1]   # only the question differs


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_answer_fenced_json_and_unknown_photo():
    got = ap.parse_answer('```json\n{"answer": "On the floor.", "object": "black bag", '
                          '"photo": 6, "box": [133, 457, 730, 702]}\n```', {5, 6})
    assert (got["photo"], got["object"], got["answer"]) == (6, "black bag", "On the floor.")
    assert ap.parse_answer('{"answer": "x", "photo": 12, "box": [1, 2, 3, 4]}', {5, 6})["photo"] is None


def test_parse_box_rejects_impossible_boxes():
    assert ap.parse_box([500, 400, 100, 600], W, H) is None
    assert ap.parse_box(None, W, H) is None
    assert ap.parse_box([100, 200, 500, 400], W, H) == pytest.approx(
        (200 * W / 1000, 100 * H / 1000, 400 * W / 1000, 500 * H / 1000))


def test_pixel_angle_centre_is_ahead_left_is_positive():
    assert ap.pixel_angle_deg(W / 2, W) == pytest.approx(0.0)
    assert ap.pixel_angle_deg(0, W) == pytest.approx(ap.HFOV_DEG / 2)
    assert ap.pixel_angle_deg(W, W) == pytest.approx(-ap.HFOV_DEG / 2)


# ── answering ───────────────────────────────────────────────────────────────

def test_no_photos_says_so(monkeypatch):
    _use(monkeypatch, _Bridge(), _LLM())
    r = ap.ask("where is my bag?")
    assert r == {"ok": False, "why": "no_photos"}
    assert "haven't taken any photos" in ap.describe(r)


def test_not_in_any_photo(monkeypatch):
    _log(4)
    _use(monkeypatch, _Bridge(), _LLM('{"answer": "I do not see a red cup.", '
                                      '"object": "red cup", "photo": null, "box": null}'))
    r = ap.ask("where is the red cup?")
    assert r["photo"] is None and r["count"] == 4
    assert "in none of them" in ap.describe(r)


def test_found_is_placed_with_that_photos_stamp_and_measured_from_now(monkeypatch):
    log = _log(3, pose=(0.0, 0.0, 0.0))
    target = log[1]
    bridge = _Bridge(pose=(1.0, 0.0, 90.0),          # moved 1 m and turned left since
                     ground={"ok": True, "at_capture": True,
                             "object": {"x": 1.0, "y": -2.0}, "goal": {"x": 1.0, "y": -1.55, "yaw": -1.57}})
    llm = _LLM(f'{{"answer": "The bag is by the wall.", "object": "black bag", '
               f'"photo": {target["n"]}, "box": [700, 400, 900, 600]}}',
               '{"box": [200, 450, 700, 700]}')
    _use(monkeypatch, bridge, llm)
    r = ap.ask("where is my black bag?")
    assert r["photo"] == target["n"]
    # step 2 refined the box, and grounding used THAT photo's stamp
    assert bridge.queries[-1][2] == target["stamp"]
    assert r["box"] == pytest.approx((450 * W / 1000, 200 * H / 1000, 700 * W / 1000, 700 * H / 1000))
    # step 2 appended to step 1: same image prefix, then the answer, then the photo again
    step1, step2 = llm.requests
    assert step2[0].content == step1[0].content
    # object at (1, -2) from (1, 0) heading 90: 2 m, straight behind
    assert r["distance_m"] == pytest.approx(2.0)
    assert abs(r["bearing_deg"]) == pytest.approx(180.0)
    assert "about 2.0 m away" in ap.describe(r) and "(" not in ap.describe(r)


def test_depth_gone_gives_a_direction_only_if_still_near_the_photo(monkeypatch):
    log = _log(1, pose=(0.0, 0.0, 0.0))
    reply = (f'{{"answer": "It is there.", "object": "box", "photo": {log[0]["n"]}, '
             f'"box": [400, 0, 600, 100]}}')
    _use(monkeypatch, _Bridge(pose=(0.1, 0.0, 30.0)), _LLM(reply, '{"box": null}'))
    r = ap.ask("where is the box?")
    assert r["bearing_only"] and r["distance_m"] is None
    # box centre u = 50 px: left of centre; heading was 0, now 30 -> bearing reduced by 30
    assert r["bearing_deg"] == pytest.approx(ap.pixel_angle_deg(0.05 * W, W) - 30.0)
    assert "can't measure its distance" in ap.describe(r)

    _use(monkeypatch, _Bridge(pose=(2.0, 0.0, 0.0)), _LLM(reply, '{"box": null}'))
    far = ap.ask("where is the box?")
    assert far["bearing_deg"] is None and not far["bearing_only"]
    assert "can't tell where it is from here" in ap.describe(far)
