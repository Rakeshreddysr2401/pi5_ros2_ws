"""Finding things, floor tests 2026-11-18 (owner's room):

  * a find that cannot be the thing is dropped and the search goes on -- under
    MIN_FIND_M across (a 4 cm scrap "found" as the black bottle), or where an
    earlier drive for it found nothing;
  * a misheard description ("the life chair" for "the white chair") that no
    view matches is asked again as the simpler words ("chair") over the photos
    already taken, and the person is told;
  * not there on arrival: one more search from there, never the same spot.
"""
import langrobo_core.tools.approach as ap
from langrobo_core.tools.approach import approach_described_object, arrival_check, simpler_words

from test_object_memory import STATE, _grounded, robot, scripted  # noqa: F401 -- fixtures


def test_simpler_words_keeps_colour_and_kind_only_when_something_else_was_said():
    assert simpler_words("the life chair") == "chair"
    assert simpler_words("the toy car") == "car"
    assert simpler_words("the white chair") is None          # nothing to drop
    assert simpler_words("black bottle on the floor") is None  # "on the floor" is where, not what
    assert simpler_words("helmet") is None


def test_a_tiny_find_is_dropped_and_the_search_goes_on(robot, scripted):
    turns, vlm = scripted
    vlm.extend([(200.0, 150.0, (195.0, 145.0, 205.0, 155.0)),    # 10 px at 0.5 m: 2 cm
                (220.0, 120.0, (170.0, 70.0, 270.0, 170.0))])    # 100 px at 1.2 m: 53 cm
    robot.replies = [_grounded(0.5, 0.0, depth=0.5), _grounded(0.0, 1.2, depth=1.2)]
    out = approach_described_object.invoke({"description": "black bottle", "state": dict(STATE)})
    assert robot.navs and robot.navs[-1][1] > 0.5, out       # drove to the second, real find
    assert len(robot.queries) == 2                            # each find measured once


def test_a_spot_already_checked_is_not_found_again(robot, scripted):
    turns, vlm = scripted
    big = (220.0, 120.0, (170.0, 70.0, 270.0, 170.0))
    vlm.extend([big, big])
    robot.replies = [_grounded(1.0, 0.0), _grounded(0.0, 1.0)]
    job = {"avoid": [(1.0, 0.05)], "retry": True, "superseded": False, "then": ""}
    out = ap._approach("black bottle", dict(STATE), job=job)
    assert robot.navs and robot.navs[-1][1] > 0.4, out
    assert job["found_at"] == (0.0, 1.0)


def test_misheard_words_are_retried_as_the_simpler_words_over_the_photos(robot, scripted, monkeypatch):
    asked = []

    def ask(question, **kw):
        asked.append(question)
        if "chair" in question and "life" not in question:
            return {"ok": True, "photo": 5, "box": (100.0, 60.0, 220.0, 200.0)}
        return {"ok": True, "photo": None, "count": 3, "oldest_s": 60, "answer": "No."}
    monkeypatch.setattr(ap._recall, "ask", ask)
    monkeypatch.setattr(ap, "_from_memory", lambda b, d: (None, None))
    monkeypatch.setattr(ap._photos, "get", lambda n: {"n": n, "jpeg": b"", "stamp": (1, 2),
                                                      "pose": (0.0, 0.0, 0.0), "when": 0.0})
    robot.replies = [_grounded(1.0, 0.3)]
    out = approach_described_object.invoke({"description": "the life chair", "state": dict(STATE)})
    assert "where is the chair?" in asked
    assert robot.navs and "found a chair" in out and robot.navs[-1][3] == "near the chair", out


def test_not_there_on_arrival_searches_once_more_never_twice(robot, monkeypatch):
    started = []
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: None)
    monkeypatch.setattr(ap._notices, "available", lambda: True)
    monkeypatch.setattr(ap, "_start_search", lambda *a, **k: started.append((a, k)) or "")
    monkeypatch.setattr(ap, "_job", {"description": "black bottle", "target": "black bottle",
                                     "found_at": (0.5, 0.0), "state": dict(STATE), "then": ""})
    said = arrival_check("black bottle")
    assert "looking around here once more" in said
    assert started and started[0][1]["retry"] is True and (0.5, 0.0) in started[0][1]["avoid"]

    monkeypatch.setattr(ap, "_job", {"description": "black bottle", "found_at": (0.6, 0.0),
                                     "retry": True, "state": dict(STATE), "then": ""})
    assert "may have moved" in arrival_check("black bottle") and len(started) == 1


def test_a_box_with_only_floor_in_it_is_not_a_find(robot, scripted):
    """'the black bottle on the floor' boxed on a marble vein (2026-11-18)."""
    turns, vlm = scripted
    big = (220.0, 120.0, (170.0, 70.0, 270.0, 170.0))
    vlm.extend([big, big])
    floor = {**_grounded(0.6, 0.0, depth=0.6), "region": False}
    robot.replies = [floor, _grounded(0.0, 1.0)]
    out = approach_described_object.invoke({"description": "black bottle", "state": dict(STATE)})
    assert robot.navs and robot.navs[-1][1] > 0.4, out


def test_the_arrival_check_does_not_accept_bare_floor(robot, monkeypatch):
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", {"stamp": (1, 2)}))
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: (220.0, 120.0, (170.0, 70.0, 270.0, 170.0)))
    monkeypatch.setattr(ap, "_job", None)
    robot.replies = [{**_grounded(0.6, 0.0, depth=0.6), "region": False}]
    assert "can't see the black bottle" in arrival_check("black bottle")


# ── the zoomed second look (2026-11-19) ──────────────────────────────────────

def _jpeg(w=448, h=252):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 90)).save(b, "JPEG")
    return b.getvalue()


def test_the_crop_is_around_the_box_and_enlarged():
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(ap.crop_for_check(_jpeg(), (200, 120, 220, 140))))
    assert max(im.size) == 448                     # 112 px around a 20 px box, enlarged


def _says(monkeypatch, word, slots=5):
    from langrobo_core.services import llm as service
    from langchain_core.messages import AIMessage
    asked = []

    class Fake:
        def invoke(self, msgs, config=None):
            asked.append(msgs)
            return AIMessage(word)
    monkeypatch.setattr(service, "get_llm", lambda *a, **k: asked.append(k) or Fake())
    monkeypatch.setattr(ap._survey, "server_slots", slots)
    return asked


def test_a_clear_no_up_close_drops_the_find(monkeypatch):
    asked = _says(monkeypatch, "No.")
    assert ap._crop_says_yes(_jpeg(), (200, 120, 220, 140), "black bottle") is False
    assert asked[0]["slot"] == ap.VERIFY_SLOT       # never the photo log's slot


def test_yes_keeps_it_and_no_spare_slot_means_no_check(monkeypatch):
    _says(monkeypatch, "Yes")
    assert ap._crop_says_yes(_jpeg(), (200, 120, 220, 140), "black bottle") is True
    asked = _says(monkeypatch, "No", slots=4)
    assert ap._crop_says_yes(_jpeg(), (200, 120, 220, 140), "black bottle") is True and not asked
