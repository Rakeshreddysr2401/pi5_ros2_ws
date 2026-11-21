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
