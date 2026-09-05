"""fastpath.py — the deterministic movement lane must match exactly the simple
commands and NOTHING ambiguous (false positives here mean the wrong physical
motion; false negatives just fall back to the LLM)."""

from langrobo_core.fastpath import FastIntent, match

KNOWN = {"kitchen", "charging_dock", "dining_area"}


def m(text):
    return match(text, known_locations=KNOWN)


# ── Stop ──────────────────────────────────────────────────────────────────────

def test_stop_variants():
    for t in ("stop", "Stop!", "halt", "stop moving", "don't move",
              "freeze", "stay there", "rakhi stop"):
        intent = m(t)
        assert intent and intent.kind == "stop", t


# ── Fine movement ─────────────────────────────────────────────────────────────

def test_move_forward_with_value():
    intent = m("go forward 50 cm")
    assert intent == FastIntent("move", {"dir": "F", "cm": 50.0})


def test_move_word_number_and_meters():
    intent = m("move forward two meters")
    assert intent == FastIntent("move", {"dir": "F", "cm": 200.0})


def test_move_back_default():
    intent = m("go back")
    assert intent == FastIntent("move", {"dir": "B", "cm": 30.0})


def test_move_a_bit():
    intent = m("move back a bit")
    assert intent.kind == "move" and intent.args["cm"] == 15.0


def test_turn_defaults_and_values():
    assert m("turn left") == FastIntent("turn", {"dir": "left", "deg": 90.0})
    assert m("turn right 45 degrees") == FastIntent("turn", {"dir": "right", "deg": 45.0})
    assert m("turn around") == FastIntent("turn", {"dir": "right", "deg": 180.0})


# ── Navigation: saved locations vs objects vs unknown ─────────────────────────

def test_goto_saved_location():
    intent = m("go to the kitchen")
    assert intent == FastIntent("goto", {"name": "kitchen"})


def test_goto_multiword_location():
    intent = m("go to the dining area")
    assert intent == FastIntent("goto", {"name": "dining area"})


def test_goto_object_becomes_approach():
    intent = m("go near the chair")
    assert intent == FastIntent("approach", {"target": "chair"})


def test_goto_object_alias():
    intent = m("go to the sofa")
    assert intent == FastIntent("approach", {"target": "couch"})


def test_goto_unknown_place_falls_through():
    assert m("go to the balcony") is None


def test_come_here_is_person_approach():
    for t in ("come here", "come to me", "come", "come near me", "find me"):
        intent = m(t)
        assert intent == FastIntent("approach", {"target": "person"}), t


def test_find_object():
    intent = m("find the bottle")
    assert intent == FastIntent("approach", {"target": "bottle"})


def test_find_unknown_falls_through():
    assert m("find my keys") is None


# ── Save / look / scan / list ────────────────────────────────────────────────

def test_save_location():
    intent = m("save this location as charging dock")
    assert intent == FastIntent("save", {"name": "charging dock"})


def test_remember_spot():
    intent = m("remember this spot as dining area")
    assert intent == FastIntent("save", {"name": "dining area"})


def test_remember_non_location_falls_through():
    assert m("remember to buy milk") is None


def test_look_directions():
    assert m("look left") == FastIntent("look", {"dir": "left"})
    assert m("look behind") == FastIntent("look", {"dir": "behind"})


def test_scan():
    for t in ("scan the room", "look around", "scan"):
        intent = m(t)
        assert intent and intent.kind == "scan", t


def test_list_locations():
    assert m("list saved locations").kind == "list"
    assert m("where can you go").kind == "list"


# ── Must NOT match (goes to the LLM) ─────────────────────────────────────────

def test_ambiguous_falls_through():
    for t in ("go to the kitchen after the song ends",
              "what's the weather",
              "can you order pizza",
              "go to sleep",
              "come on that's funny",
              "stop the music",       # music stop is chat's job
              "look at this mess",
              "tell me a joke"):
        assert m(t) is None, t


# ── Execution wiring (StubBridge) ─────────────────────────────────────────────

def test_try_handle_speaks_and_returns_text():
    from langrobo_core.bridges import StubBridge
    from langrobo_core.tools import _bridge
    from langrobo_core import fastpath

    _bridge._instance = None
    bridge = StubBridge(known_locations={"kitchen": (2.5, 1.0, 0.0)})
    _bridge.init(bridge)

    spoken = fastpath.try_handle("go to the kitchen")
    assert spoken and "kitchen" in spoken.lower()

    # Non-command returns None untouched
    assert fastpath.try_handle("what's the weather like") is None


def test_try_handle_approach_with_detection():
    from langrobo_core.bridges import StubBridge
    from langrobo_core.tools import _bridge
    from langrobo_core import fastpath

    from langrobo_core.services import world_model
    from langrobo_core.services.world_model import WorldModel

    _bridge._instance = None
    _bridge.init(StubBridge())
    world_model.reset(WorldModel(path=None))
    world_model.get().observe("person", 2.0, 0.0, 0.4, 0.9)

    spoken = fastpath.try_handle("come here")
    assert spoken and "on my way" in spoken.lower()
