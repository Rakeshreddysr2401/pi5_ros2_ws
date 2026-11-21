"""frame_memory -- "where did I see X?" over EVERYTHING the robot has seen (docs/plans/FRAME_MEMORY.md).

The Jetson keeps every keyframe (photo + depth + camera pose at its stamp); the Mac embeds them with
EmbeddingGemma 2 and runs the find chain (scripts/mac_mini/kino_find.py: search -> OWLv2 -> EG2 check ->
Gemma 4 close-up only when unsure). This module asks the Mac, then asks the Jetson to place the box on
THAT frame's own depth and pose (pixel_to_goal, any age) -- so the answer is where the thing is from
wherever the robot is NOW.

    sighting(bridge, "the helmet") -> (answer, raw)
        answer: photo_recall.ask's shape, so approach.py's steps after it are unchanged:
                {"ok", "photo": None, "from_frames": True, "age_s", "distance_m", "bearing_deg",
                 "placed": {"x", "y"} (odom), "map": {"x", "y"} | None, "frame_id", "box", "decided_by"}
                or None (not seen, or the Mac / the Jetson did not answer)
        raw:    the Mac's reply (incl. "maybe": an honest near miss Gemma named, for the caller to mention)

A hint, never a fact: things move. approach.py drives to a viewpoint and LOOKS before going near.
Off with LANGROBO_FRAME_MEMORY=0. Never raises: the other ways of finding still work without it.
"""
import json
import logging
import math
import os
import urllib.request

logger = logging.getLogger(__name__)

URL = os.environ.get('LANGROBO_FRAME_MEMORY_URL', 'http://singireddys-mac-mini.local:8095').rstrip('/')
ENABLED = os.environ.get('LANGROBO_FRAME_MEMORY', '1') != '0'
FIND_TIMEOUT_S = float(os.environ.get('LANGROBO_FRAME_MEMORY_TIMEOUT_S', '60'))


def _find(words: str) -> dict | None:
    body = json.dumps({'words': words, 'frames': 6, 'enough': 1}).encode()
    req = urllib.request.Request(URL + '/find', body, {'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=FIND_TIMEOUT_S))


def clean_words(description: str) -> str:
    """'the helmet' / 'my helmet' -> 'a helmet' (the search wording measured in the bake-off)."""
    w = description.strip().rstrip('?.!')
    for lead in ('the ', 'my ', 'our ', 'your ', 'a ', 'an '):
        if w.lower().startswith(lead):
            w = w[len(lead):]
            break
    return ('an ' if w[:1].lower() in 'aeiou' else 'a ') + w if w else w


def to_answer(hit: dict, grounded: dict) -> dict | None:
    """The Mac's hit + the Jetson's placement -> photo_recall.ask's shape (None if not placed)."""
    if not grounded or not grounded.get('ok') or not grounded.get('object'):
        return None
    rel = grounded.get('relative') or {}
    fwd, left = rel.get('forward_m'), rel.get('left_m')
    dist = math.hypot(fwd, left) if fwd is not None and left is not None else None
    return {'ok': True, 'photo': None, 'from_frames': True,
            'age_s': float(hit.get('age_s') or 0.0),
            'distance_m': None if dist is None else round(dist, 2),
            'bearing_deg': rel.get('bearing_deg'),
            'placed': {'x': grounded['object']['x'], 'y': grounded['object']['y']},
            'map': grounded.get('object_map'), 'frame_id': hit['id'], 'box': hit['box'],
            'decided_by': hit.get('decided_by'), 'remembered': grounded.get('remembered', False)}


def sighting(bridge, description: str) -> tuple:
    if not ENABLED:
        return None, None
    words = clean_words(description)
    try:
        raw = _find(words)
    except Exception as e:
        logger.warning('frame memory: the Mac did not answer for %r: %s', words, e)
        return None, None
    found = (raw or {}).get('found') or []
    for hit in found:                                   # newest sighting first
        stamp_ns = int(hit['stamp_ns'])
        x0, y0, x1, y1 = hit['box']
        try:
            g = bridge.ground_pixel((x0 + x1) / 2, (y0 + y1) / 2, timeout=6.0,
                                    stamp=(stamp_ns // 10**9, stamp_ns % 10**9), box=(x0, y0, x1, y1),
                                    what=description)
        except Exception as e:
            logger.warning('frame memory: grounding failed for %r: %s', words, e)
            continue
        ans = to_answer(hit, g)
        if ans is not None:
            logger.info('frame memory: %r seen %.0f s ago, %s m away at %s deg (%s)', words, ans['age_s'],
                        ans['distance_m'], ans['bearing_deg'], ans['decided_by'])
            return ans, raw
        logger.info('frame memory: %r found in frame %s but not placed: %s', words, hit['id'],
                    (g or {}).get('reason'))
    return None, raw


# ── the same check on a LIVE photo (the robot's fresh look) ──────────────────
LOOK_TIMEOUT_S = float(os.environ.get('LANGROBO_FRAME_MEMORY_LOOK_TIMEOUT_S', '30'))


def check_photo(jpeg: bytes, description: str):
    """Is `description` in this photo? The Mac's checked chain (OWLv2 -> EmbeddingGemma 2 -> Gemma close-up
    only when unsure; on the 36 labelled cases: 15/20 found, every box right, 0/16 made up -- the old
    Gemma-only look made up 11/16). Returns (u, v, box) like approach._vlm_locate, or None (not there).
    Raises when the Mac cannot be asked -- the caller then looks the old way."""
    import base64
    if not ENABLED:
        raise RuntimeError('frame memory off')
    body = json.dumps({'words': clean_words(description), 'jpeg': base64.b64encode(jpeg).decode()}).encode()
    req = urllib.request.Request(URL + '/find_in', body, {'Content-Type': 'application/json'})
    r = json.load(urllib.request.urlopen(req, timeout=LOOK_TIMEOUT_S))
    if not r.get('ok'):
        raise RuntimeError(r.get('reason') or 'find_in failed')
    f = r.get('found')
    if not f:
        return None
    x0, y0, x1, y1 = f['box']
    return ((x0 + x1) / 2, (y0 + y1) / 2, (x0, y0, x1, y1))


# ── "where is X?" answered from memory, without moving ───────────────────────
def _direction(bearing_deg) -> str:
    if bearing_deg is None:
        return ''
    b = float(bearing_deg)
    side = 'left' if b > 0 else 'right'
    a = abs(b)
    if a < 15:
        return 'straight ahead of me'
    if a < 60:
        return f'ahead, to my {side}'
    if a < 120:
        return f'to my {side}'
    return f'behind me, on my {side}'


def _age(seconds) -> str:
    s = max(0, int(seconds or 0))
    if s < 60:
        return 'just now' if s < 10 else f'{s} seconds ago'
    if s < 3600:
        m = s // 60
        return f'{m} minute{"s" if m != 1 else ""} ago'
    h, m = s // 3600, s % 3600 // 60
    return f'{h} hour{"s" if h != 1 else ""} {m} minutes ago' if m else f'{h} hour{"s" if h != 1 else ""} ago'


def where_text(thing: str, ans: dict | None, raw: dict | None) -> str:
    """The spoken answer. Never claims more than was checked."""
    name = thing.strip().rstrip('?.!')
    if ans:
        d = ans.get('distance_m')
        where = (f'about {d:.1f} m from here, {_direction(ans.get("bearing_deg"))}' if d is not None
                 else _direction(ans.get('bearing_deg')) or 'but I could not measure where')
        return (f'I last saw {name} {_age(ans.get("age_s"))}, {where}. '
                'Things get moved, so I would check before going there.')
    maybe = (raw or {}).get('maybe')
    if maybe and maybe.get('looks_like'):
        return (f"I'm not sure I've seen {name}. The closest thing I saw was {maybe['looks_like']}, "
                f"{_age(maybe.get('age_s'))}. Is that it?")
    if raw is None:
        return "I can't reach my memory of what I've seen right now, so I can't say where it is."
    return f"I haven't seen {name} in what I remember. I can go and look for it if you like."


try:
    from langchain_core.tools import tool
except Exception:                                    # tests without langchain
    def tool(f):
        return f


@tool
def last_seen(thing: str) -> str:
    """Where is something the robot has SEEN (on any day), without moving: how long ago, how far and which way
    from here. Use for "where is my helmet?", "where did you see the keys?", "have you seen my bag?".
    thing: the object as the person said it ("my helmet", "the black bag").
    To actually go there, the navigate agent's approach_described_object does that."""
    from . import _bridge
    ans, raw = sighting(_bridge.get(), thing)
    return where_text(thing, ans, raw)
