"""ask_photos + frame memory, and sending a remembered photo (2026-10-29).

Owner: "if I ask on Telegram for something it saw while roaming the last 5-10
minutes, it should say where, and send that picture". The photo log (24
photos) first; then the frame memory (keyframe_node); a remembered frame the
VLM confirms joins the photo log and is placed like any photo.
"""
import base64
import io
import math
import time
import types

import pytest
from PIL import Image

from langrobo_core.tools import photo_recall as pr
from langrobo_core.tools import photos
from langrobo_core.tools import telegram as tg


def _jpeg(color=(40, 120, 200)):
    out = io.BytesIO()
    Image.new("RGB", (448, 252), color).save(out, format="JPEG")
    return out.getvalue()


def _hit(conf="strong", age=240.0, yaw_rad=math.pi / 2, color=(40, 120, 200)):
    return {"stamp": {"sec": 1793290000, "nanosec": 5}, "age_s": age, "confidence": conf,
            "margin": 0.1, "robot": {"x": 1.0, "y": 2.0, "yaw": yaw_rad},
            "width": 448, "height": 252, "jpeg": base64.b64encode(_jpeg(color)).decode()}


class FakeBridge:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def keyframe_query(self, text, k=2, timeout=4.0):
        self.asked.append(text)
        return self.reply

    def get_origin_epoch(self):
        return 7.0


@pytest.fixture
def world(monkeypatch):
    """A bridge with frame memory, and ask_log faked: the log never has it;
    a memory photo is confirmed only if its colour is the 'right' one."""
    photos.clear()
    state = {"bridge": None, "confirm": {(40, 120, 200)}, "calls": []}

    def fake_ask_log(question, only=None, place=True):
        state["calls"].append((question, only))
        if only is None:
            return {"ok": True, "answer": "No.", "object": "the bottle", "photo": None,
                    "count": 3, "oldest_s": 120.0}
        rec = photos.get(only)
        px = Image.open(io.BytesIO(rec["jpeg"])).getpixel((10, 10))
        seen = any(all(abs(a - b) < 12 for a, b in zip(px, c)) for c in state["confirm"])
        if not seen:
            return {"ok": True, "answer": "No.", "object": None, "photo": None, "count": 4,
                    "oldest_s": 120.0}
        rec["box"] = (100.0, 50.0, 160.0, 140.0)
        return {"ok": True, "answer": "Yes.", "object": None, "photo": only, "count": 4,
                "oldest_s": 120.0, "age_s": time.time() - rec["when"], "box": rec["box"],
                "width": 448, "distance_m": 2.1, "bearing_deg": 150.0, "bearing_only": False}
    monkeypatch.setattr(pr, "ask_log", fake_ask_log)

    def use(reply):
        state["bridge"] = FakeBridge(reply)
        monkeypatch.setattr(pr._bridge, "get", lambda: state["bridge"])
        return state
    yield use
    photos.clear()


def test_memory_is_only_asked_by_ask_photos(world):
    s = world({"ok": True, "frames": 40, "hits": [_hit()]})
    out = pr.ask("where is the bottle?")                 # approach / look_toward: unchanged
    assert out["photo"] is None and s["bridge"].asked == []


def test_a_confirmed_memory_frame_answers_and_joins_the_photo_log(world):
    s = world({"ok": True, "frames": 40, "hits": [_hit()]})
    out = pr.ask("where is the bottle?", memory=True)
    assert out["from_memory"] is True and out["photo"] is not None
    assert s["bridge"].asked == ["the bottle"]          # the object, not the whole question
    rec = photos.get(out["photo"])
    assert rec["source"] == "memory" and rec["stamp"] == (1793290000, 5)
    assert rec["pose"] == pytest.approx((1.0, 2.0, 90.0))   # heading in DEGREES, like look()
    assert time.time() - rec["when"] == pytest.approx(240.0, abs=2)
    said = pr.describe(out)
    assert said.startswith("I saw the bottle 4 min ago (photo")
    assert "from what I saw in the last 10 minutes" in said
    assert "about 2.1 m away" in said


def test_weak_hits_are_never_shown_to_the_vlm(world):
    s = world({"ok": True, "frames": 40, "hits": [_hit(conf="weak")]})
    out = pr.ask("where is the bottle?", memory=True)
    assert out["photo"] is None and [c for c in s["calls"] if c[1] is not None] == []
    assert "nor in what I saw in the last 10 minutes" in pr.describe(out)


def test_a_hit_the_vlm_rejects_falls_to_the_next(world):
    s = world({"ok": True, "frames": 40,
               "hits": [_hit(color=(200, 30, 30)), _hit(color=(40, 120, 200))]})
    out = pr.ask("where is the bottle?", memory=True)
    assert out["from_memory"] and len([c for c in s["calls"] if c[1] is not None]) == 2


def test_no_frame_memory_answers_as_before(world):
    world({"ok": False, "reason": "no_frame_memory"})
    out = pr.ask("where is the bottle?", memory=True)
    said = pr.describe(out)
    assert out["photo"] is None and "last 10 minutes" not in said


def test_no_photos_yet_still_checks_memory(world, monkeypatch):
    s = world({"ok": True, "frames": 12, "hits": [_hit()]})
    real = pr.ask_log

    def empty_log(question, only=None, place=True):
        if only is None:
            return {"ok": False, "why": "no_photos"}
        return real(question, only=only, place=place)
    monkeypatch.setattr(pr, "ask_log", empty_log)
    out = pr.ask("where is my bag?", memory=True)
    assert out.get("from_memory") and s["bridge"].asked == ["where is my bag?"]


# ── sending it ──────────────────────────────────────────────────────────────

def test_the_box_is_drawn():
    plain = _jpeg((250, 250, 250))
    marked = tg.boxed(plain, (100, 50, 160, 140))
    img = Image.open(io.BytesIO(marked))
    r, g, b = img.getpixel((96, 90))            # on the box's left edge (4 px pad)
    assert r > 200 and g < 120 and b < 120
    assert tg.boxed(plain, None) == plain


class FakeSvc:
    def __init__(self):
        self.sent = []

    def send_photo(self, chat_id, jpeg, caption):
        self.sent.append((chat_id, jpeg, caption))
        return None


def test_send_photo_n_sends_that_photo_boxed():
    photos.clear()
    rec = photos.record(_jpeg(), (1, 2), (0.0, 0.0, 0.0), time.time() - 300, 7.0, "memory")
    rec["box"] = (100.0, 50.0, 160.0, 140.0)
    svc, member = FakeSvc(), types.SimpleNamespace(chat_id=42, name="Rakesh")
    reply = tg._send_logged_photo(svc, member, "Rakesh", rec["n"], "")
    assert svc.sent and svc.sent[0][0] == 42
    assert svc.sent[0][1] != rec["jpeg"]                  # the box is on it
    assert svc.sent[0][2] == f"Photo {rec['n']}, 5 min ago"
    assert "red box" in reply and "not the view now" in reply
    photos.clear()


def test_a_photo_no_longer_kept_says_so():
    photos.clear()
    svc, member = FakeSvc(), types.SimpleNamespace(chat_id=42, name="Rakesh")
    reply = tg._send_logged_photo(svc, member, "Rakesh", 999, "")
    assert svc.sent == [] and "no longer have photo 999" in reply


@pytest.mark.parametrize("obj,said", [("bed", "the bed"), ("the bottle", "the bottle"),
                                      ("my bag", "my bag"), ("a red cup", "a red cup"),
                                      (None, "it"), ("bed?", "the bed")])
def test_the_thing_is_named_with_an_article(obj, said):
    assert pr._the(obj) == said
