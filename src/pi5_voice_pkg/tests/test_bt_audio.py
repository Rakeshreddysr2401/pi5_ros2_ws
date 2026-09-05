"""Bluetooth routing helpers: name mangling, wpctl parsing, graceful failure.

No Bluetooth hardware, no PipeWire, no network — the parsing is fed recorded
output and every shell call is stubbed.
"""

import pytest

from pi5_voice_pkg import bt_audio


# Real `wpctl status` shape, with a Boat Stone connected as an A2DP sink.
WPCTL = """PipeWire 'pipewire-0' [1.0.5, rakhi24@rakhi24-desktop, cookie:12345]
 └─ Clients:
        32. WirePlumber                         [pid:1443]

Audio
 ├─ Devices:
 │      41. Built-in Audio
 │      55. boAt Stone
 │
 ├─ Sinks:
 │      33. Dummy Output                        [vol: 1.00 MUTED]
 │  *   57. boAt Stone 350                      [vol: 0.74]
 │
 ├─ Sources:
 │      35. Plantronics Blackwire 3220          [vol: 1.00]
 │
 ├─ Streams:
"""


def test_mac_becomes_a_pipewire_suffix():
    assert bt_audio.mac_suffix("aa:bb:cc:dd:ee:ff") == "AA_BB_CC_DD_EE_FF"
    assert bt_audio.card_name("aa:bb:cc:dd:ee:ff") == "bluez_card.AA_BB_CC_DD_EE_FF"


@pytest.mark.parametrize("value,ok", [
    ("AA:BB:CC:DD:EE:FF", True),
    ("aa:bb:cc:dd:ee:ff", True),
    ("Blackwire", False),
    ("AA:BB:CC:DD:EE", False),
    ("", False),
    (None, False),
])
def test_mac_validation(value, ok):
    assert bt_audio.is_mac(value) is ok


def test_status_parses_sections_and_default():
    st = bt_audio.parse_status(WPCTL)
    assert [n["name"] for n in st["Sinks"]] == ["Dummy Output", "boAt Stone 350"]
    stone = st["Sinks"][1]
    assert stone["id"] == 57 and stone["default"] is True
    assert st["Sinks"][0]["default"] is False
    assert st["Sources"][0]["name"] == "Plantronics Blackwire 3220"


def test_status_strips_volume_decoration():
    st = bt_audio.parse_status(WPCTL)
    assert all("[vol" not in n["name"] for n in st["Sinks"])


def test_find_node_is_case_insensitive():
    st = bt_audio.parse_status(WPCTL)
    assert bt_audio.find_node(st, "Sinks", "boat stone")["id"] == 57
    assert bt_audio.find_node(st, "Sinks", "nothing here") is None
    assert bt_audio.find_node(st, "Sinks", "") is None


def test_parsing_survives_junk():
    assert bt_audio.parse_status("") == {}
    assert bt_audio.parse_status("not\nwpctl\noutput") == {}


# ── Degradation (hard rule 4) ───────────────────────────────────────────────

def test_disabled_when_no_mac_configured():
    r = bt_audio.ensure("")
    assert r["enabled"] is False and r["connected"] is False


def test_a_bad_mac_is_reported_not_raised(monkeypatch):
    r = bt_audio.ensure("Blackwire")
    assert r["connected"] is False
    assert any("not a MAC" in n for n in r["notes"])


def test_failed_connect_stops_before_touching_pipewire(monkeypatch):
    calls = []
    monkeypatch.setattr(bt_audio, "bt_connect", lambda mac: (False, "br-connection-profile-unavailable"))
    monkeypatch.setattr(bt_audio, "wpctl_status", lambda: calls.append("wpctl") or {})
    r = bt_audio.ensure("AA:BB:CC:DD:EE:FF")
    assert r["connected"] is False and calls == []


def test_successful_a2dp_sets_the_sink_as_default(monkeypatch):
    defaults = []
    monkeypatch.setattr(bt_audio, "bt_connect", lambda mac: (True, "connected"))
    monkeypatch.setattr(bt_audio, "wpctl_status",
                        lambda: bt_audio.parse_status(WPCTL.replace("boAt Stone 350",
                                                                    "AA_BB_CC_DD_EE_FF")))
    monkeypatch.setattr(bt_audio, "set_default", lambda i: defaults.append(i) or (True, ""))
    monkeypatch.setattr(bt_audio.shutil, "which", lambda x: None)   # no pactl
    r = bt_audio.ensure("AA:BB:CC:DD:EE:FF", "a2dp")
    assert defaults == [57] and r["sink"] == "AA_BB_CC_DD_EE_FF"


def test_hfp_without_pactl_says_which_package_is_missing(monkeypatch):
    monkeypatch.setattr(bt_audio.shutil, "which", lambda x: None)
    ok, msg = bt_audio.set_profile("AA:BB:CC:DD:EE:FF", "hfp")
    assert ok is False and "pulseaudio-utils" in msg


def test_missing_sink_is_reported_rather_than_crashing(monkeypatch):
    monkeypatch.setattr(bt_audio, "bt_connect", lambda mac: (True, "connected"))
    monkeypatch.setattr(bt_audio, "wpctl_status", lambda: bt_audio.parse_status(WPCTL))
    monkeypatch.setattr(bt_audio.shutil, "which", lambda x: None)
    r = bt_audio.ensure("AA:BB:CC:DD:EE:FF")
    assert r["sink"] is None
    assert any("no PipeWire sink" in n for n in r["notes"])


def test_a_missing_binary_never_raises(monkeypatch):
    monkeypatch.setattr(bt_audio.shutil, "which", lambda x: None)
    rc, out = bt_audio._run(["definitely-not-installed"])
    assert rc == 127 and "not installed" in out


# ── Regression: what the first run against real hardware exposed ───────────

# `wpctl status` shows node.description (the friendly name), never node.name.
REAL_STATUS = """Audio
 ├─ Sinks:
 │  *   72. boAt Stone 650                      [vol: 0.33]
 │
 ├─ Sources:
"""

# `wpctl inspect 72` on the same speaker.
REAL_INSPECT = '''Id: 72
Global properties:
  * object.serial = "72"
Properties:
    api.bluez5.address = "D6:AA:BB:59:EF:B6"
  * media.class = "Audio/Sink"
  * node.description = "boAt Stone 650"
  * node.name = "bluez_output.D6_AA_BB_59_EF_B6.1"
'''


def test_inspect_properties_are_parsed():
    props = bt_audio.parse_inspect(REAL_INSPECT)
    assert props["api.bluez5.address"] == "D6:AA:BB:59:EF:B6"
    assert props["node.name"] == "bluez_output.D6_AA_BB_59_EF_B6.1"
    assert props["media.class"] == "Audio/Sink"


def test_mac_suffix_alone_does_not_find_a_real_sink():
    """The bug: the visible name is 'boAt Stone 650', so suffix matching misses."""
    st = bt_audio.parse_status(REAL_STATUS)
    assert bt_audio.find_node(st, "Sinks", "D6_AA_BB_59_EF_B6") is None


def test_bt_node_is_found_via_bluez_address(monkeypatch):
    st = bt_audio.parse_status(REAL_STATUS)
    monkeypatch.setattr(bt_audio, "node_properties",
                        lambda i: bt_audio.parse_inspect(REAL_INSPECT))
    node = bt_audio.find_bt_node(st, "Sinks", "D6:AA:BB:59:EF:B6")
    assert node is not None and node["id"] == 72


def test_bt_node_ignores_a_different_speaker(monkeypatch):
    st = bt_audio.parse_status(REAL_STATUS)
    monkeypatch.setattr(bt_audio, "node_properties",
                        lambda i: bt_audio.parse_inspect(REAL_INSPECT))
    assert bt_audio.find_bt_node(st, "Sinks", "AA:BB:CC:DD:EE:FF") is None
