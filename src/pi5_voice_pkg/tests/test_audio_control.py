"""audio_control: volume arithmetic and spoken device names (pure, no hardware)."""
from pi5_voice_pkg import audio_control as ac

# What bluez has paired on the rover's Pi 5 (2026-10-04).
DEVICES = [
    {"mac": "D6:AA:BB:59:EF:B6", "name": "boAt Stone 650", "connected": True},
    {"mac": "84:0F:2A:4C:CD:38", "name": "OnePlus Buds Z2", "connected": False},
    {"mac": "D2:66:3D:F3:53:24", "name": "ASUS KW100 Channel 1", "connected": False},
]


def test_parse_volume():
    assert ac.parse_volume("Volume: 0.65") == (65, False)
    assert ac.parse_volume("Volume: 1.00 [MUTED]") == (100, True)
    assert ac.parse_volume("") == (None, False)
    assert ac.parse_volume("Object not found") == (None, False)


def test_volume_target():
    assert ac.volume_target(60, change=10) == 70
    assert ac.volume_target(95, change=10) == 100          # capped: the Stone distorts above 100
    assert ac.volume_target(5, change=-10) == 0
    assert ac.volume_target(60, set_to=40) == 40           # "volume 40" wins over a change
    assert ac.volume_target(60, set_to=150) == 100
    assert ac.volume_target(None, change=10) == 60         # unreadable counts as 50
    assert ac.volume_target(60, change=0) == 60


def test_match_spoken_names():
    for spoken, want in (("my buds", "OnePlus Buds Z2"), ("oneplus", "OnePlus Buds Z2"),
                         ("the boat", "boAt Stone 650"), ("boAt Stone 650", "boAt Stone 650"),
                         ("stone speaker", "boAt Stone 650"), ("asus", "ASUS KW100 Channel 1"),
                         ("84:0f:2a:4c:cd:38", "OnePlus Buds Z2")):
        dev, tied = ac.match_device(spoken, DEVICES)
        assert dev and dev["name"] == want, (spoken, dev, tied)


def test_match_nothing_or_ambiguous():
    assert ac.match_device("my car", DEVICES) == (None, [])
    assert ac.match_device("", DEVICES) == (None, [])
    two = DEVICES + [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Galaxy Buds"}]
    dev, tied = ac.match_device("buds", two)
    assert dev is None and {d["name"] for d in tied} == {"OnePlus Buds Z2", "Galaxy Buds"}


def test_describe_devices_marks_the_one_in_use():
    out = ac.describe_devices(DEVICES, "d6:aa:bb:59:ef:b6")
    assert [d["in_use"] for d in out] == [True, False, False]
    assert out[1] == {"name": "OnePlus Buds Z2", "mac": "84:0F:2A:4C:CD:38",
                      "connected": False, "in_use": False}
