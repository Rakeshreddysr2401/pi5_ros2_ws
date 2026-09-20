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


# ── bluez discovery + which device wins ─────────────────────────────────────

DEVICES = """Device D2:66:3D:F3:53:24 ASUS KW100 Channel 1
Device D6:AA:BB:59:EF:B6 boAt Stone 650
Device 11:22:33:44:55:66 Sony WH-1000XM4
"""

# `bluetoothctl info` on the Stone after a reboot: trusted, link key gone.
INFO_STONE = """Device D6:AA:BB:59:EF:B6 (public)
	Name: boAt Stone 650
	Alias: boAt Stone 650
	Class: 0x00240414
	Paired: no
	Bonded: no
	Trusted: yes
	Blocked: no
	Connected: no
	UUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)
	UUID: Handsfree                 (0000111e-0000-1000-8000-00805f9b34fb)
"""

INFO_KEYBOARD = """Device D2:66:3D:F3:53:24 (public)
	Name: ASUS KW100 Channel 1
	Paired: yes
	Trusted: yes
	Connected: yes
	UUID: Human Interface Device    (00001812-0000-1000-8000-00805f9b34fb)
"""


def test_device_lines_are_parsed():
    devs = bt_audio.parse_devices(DEVICES)
    assert [d["mac"] for d in devs] == [
        "D2:66:3D:F3:53:24", "D6:AA:BB:59:EF:B6", "11:22:33:44:55:66"]
    assert devs[1]["name"] == "boAt Stone 650"
    assert bt_audio.parse_devices("") == []


def test_info_reads_pairing_state_and_audio_roles():
    stone = bt_audio.parse_info(INFO_STONE)
    assert stone["name"] == "boAt Stone 650"
    assert stone["paired"] is False and stone["trusted"] is True
    assert stone["connected"] is False
    assert stone["audio"] is True and stone["mic"] is True
    kb = bt_audio.parse_info(INFO_KEYBOARD)
    assert kb["audio"] is False and kb["mic"] is False


# An unpaired device fresh from a scan: no UUIDs yet, only class + icon.
INFO_SCANNED_BUDS = """Device 84:0F:2A:4C:CD:38 (public)
\tName: OnePlus Buds Z2
\tClass: 0x00240404 (2360324)
\tIcon: audio-headset
\tPaired: no
\tTrusted: no
\tConnected: no
"""


def test_a_scanned_but_unpaired_device_is_recognised_as_audio_by_its_class():
    d = bt_audio.parse_info(INFO_SCANNED_BUDS)
    assert d["audio"] is True and d["paired"] is False
    phone = bt_audio.parse_info("\tName: Pixel\n\tClass: 0x005a020c (5898764)\n\tIcon: phone\n")
    assert phone["audio"] is False


def _dev(mac, name, connected=False, audio=True, mic=True):
    return {"mac": mac, "name": name, "connected": connected,
            "audio": audio, "mic": mic, "paired": True, "trusted": True}


STONE = "D6:AA:BB:59:EF:B6"
SONY = "11:22:33:44:55:66"
KEYBOARD = "D2:66:3D:F3:53:24"


def test_non_audio_devices_are_never_candidates():
    ranked = bt_audio.rank_devices(
        [_dev(KEYBOARD, "keyboard", connected=True, audio=False, mic=False),
         _dev(STONE, "stone")], [])
    assert [d["mac"] for d in ranked] == [STONE]


def test_whatever_is_switched_on_beats_the_preferred_device_that_is_off():
    """The owner said: 'not sure I always connect the boAt Stone, or some
    other headphones' — a connected pair of headphones wins over an absent
    preferred speaker."""
    ranked = bt_audio.rank_devices(
        [_dev(STONE, "stone", connected=False), _dev(SONY, "sony", connected=True)],
        preferred=[STONE])
    assert [d["mac"] for d in ranked] == [SONY, STONE]


def test_preferred_order_breaks_ties_between_connected_devices():
    ranked = bt_audio.rank_devices(
        [_dev(SONY, "sony", connected=True), _dev(STONE, "stone", connected=True)],
        preferred=[STONE])
    assert [d["mac"] for d in ranked] == [STONE, SONY]


def test_the_device_in_use_is_kept_while_it_stays_connected():
    """Two headphones both on must not make the robot flap between them."""
    ranked = bt_audio.rank_devices(
        [_dev(SONY, "sony", connected=True), _dev(STONE, "stone", connected=True)],
        preferred=[STONE], current=SONY)
    assert ranked[0]["mac"] == SONY


def test_a_disconnected_current_device_loses_its_hold():
    ranked = bt_audio.rank_devices(
        [_dev(SONY, "sony", connected=False), _dev(STONE, "stone", connected=True)],
        preferred=[], current=SONY)
    assert ranked[0]["mac"] == STONE


def test_mac_matching_is_case_insensitive():
    ranked = bt_audio.rank_devices(
        [_dev(SONY, "sony"), _dev(STONE.lower(), "stone")], preferred=[STONE])
    assert ranked[0]["mac"] == STONE.lower()


def test_gain_overrides_are_per_device_and_forgiving():
    g = bt_audio.parse_gain_overrides(["d6:aa:bb:59:ef:b6=4.0", "", "junk", "AA:BB:CC:DD:EE:FF=x", None])
    assert g == {"D6:AA:BB:59:EF:B6": 4.0}
    assert bt_audio.parse_gain_overrides(None) == {}


@pytest.mark.parametrize("mic,prefer,expected", [
    (True, True, "hfp"),      # has a mic and we want it
    (True, False, "a2dp"),    # has a mic, owner prefers playback quality
    (False, True, "a2dp"),    # no mic to switch to
])
def test_profile_follows_mic_availability_and_preference(mic, prefer, expected):
    assert bt_audio.profile_for({"mic": mic}, prefer) == expected


def test_known_devices_merges_paired_and_trusted(monkeypatch):
    """The Stone after a reboot is Trusted but not Paired; it must still be
    a candidate, and a device in both lists appears once."""
    outputs = {"Paired": "Device 11:22:33:44:55:66 Sony WH-1000XM4\n",
               "Trusted": DEVICES}
    monkeypatch.setattr(bt_audio, "_run",
                        lambda cmd, timeout=0: (0, outputs.get(cmd[-1], "")))
    macs = [d["mac"] for d in bt_audio.known_devices()]
    assert macs.count(SONY) == 1 and STONE in macs and KEYBOARD in macs


# ── Degradation (hard rule 4) ───────────────────────────────────────────────

def test_hfp_without_pactl_says_which_package_is_missing(monkeypatch):
    monkeypatch.setattr(bt_audio.shutil, "which", lambda x: None)
    ok, msg = bt_audio.set_profile("AA:BB:CC:DD:EE:FF", "hfp")
    assert ok is False and "pulseaudio-utils" in msg


def test_a_refused_pairing_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(bt_audio, "_run",
                        lambda cmd, timeout=0: (1, "Failed to pair: org.bluez.Error.AuthenticationFailed"))
    ok, msg = bt_audio.bt_pair(STONE)
    assert ok is False and "AuthenticationFailed" in msg


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
