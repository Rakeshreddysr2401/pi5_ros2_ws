"""frame_memory: the Mac's find + the Jetson's placement -> approach.py's sighting shape. No network."""
import langrobo_core.tools.frame_memory as fm


class FakeBridge:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None, what=None):
        self.calls.append({'u': u, 'v': v, 'stamp': stamp, 'box': box, 'what': what})
        return self.reply


HIT = {'id': 1795204846315398926, 'stamp_ns': 1795204846315398926, 'age_s': 786.0,
       'box': [48, 94, 170, 147], 'margin': 0.16, 'decided_by': 'embedding'}
PLACED = {'ok': True, 'object': {'x': 0.015, 'y': -0.889, 'z': 0.1},
          'relative': {'forward_m': 0.634, 'left_m': 0.25, 'bearing_deg': 21.5},
          'object_map': {'x': 0.4, 'y': -1.3}, 'remembered': True}


def test_clean_words():
    assert fm.clean_words('the helmet') == 'a helmet'
    assert fm.clean_words('my orange bottle?') == 'an orange bottle'
    assert fm.clean_words('dustbin') == 'a dustbin'


def test_found_and_placed(monkeypatch):
    monkeypatch.setattr(fm, '_find', lambda w: {'found': [HIT], 'maybe': None})
    b = FakeBridge(PLACED)
    ans, raw = fm.sighting(b, 'the helmet')
    assert ans['ok'] and ans['from_frames'] and ans['photo'] is None
    assert ans['placed'] == {'x': 0.015, 'y': -0.889}
    assert abs(ans['distance_m'] - 0.68) < 0.01 and ans['bearing_deg'] == 21.5
    assert ans['map'] == {'x': 0.4, 'y': -1.3} and ans['age_s'] == 786.0
    # grounded on THAT frame: its stamp split into (sec, nanosec), its box, the box centre
    c = b.calls[0]
    assert c['stamp'] == (1795204846, 315398926) and c['box'] == (48, 94, 170, 147)
    assert (c['u'], c['v']) == (109.0, 120.5)


def test_not_placed_tries_next_then_none(monkeypatch):
    older = {**HIT, 'id': 1, 'stamp_ns': 1795204000000000000}
    monkeypatch.setattr(fm, '_find', lambda w: {'found': [HIT, older]})
    b = FakeBridge({'ok': False, 'reason': 'remembered_without_depth'})
    ans, raw = fm.sighting(b, 'helmet')
    assert ans is None and len(b.calls) == 2           # both sightings tried, none placed


def test_near_miss_passed_through(monkeypatch):
    maybe = {'looks_like': 'orange rectangular bottle', 'age_s': 30}
    monkeypatch.setattr(fm, '_find', lambda w: {'found': [], 'maybe': maybe})
    ans, raw = fm.sighting(FakeBridge(PLACED), 'water bottle')
    assert ans is None and raw['maybe']['looks_like'] == 'orange rectangular bottle'


def test_mac_down_is_not_an_error(monkeypatch):
    def boom(w):
        raise OSError('connection refused')
    monkeypatch.setattr(fm, '_find', boom)
    assert fm.sighting(FakeBridge(PLACED), 'helmet') == (None, None)


def test_switched_off(monkeypatch):
    monkeypatch.setattr(fm, 'ENABLED', False)
    monkeypatch.setattr(fm, '_find', lambda w: 1 / 0)
    assert fm.sighting(FakeBridge(PLACED), 'helmet') == (None, None)
