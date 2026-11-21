#!/usr/bin/env python3
"""kino_find -- "where is X?" over the robot's frame memory, on the Mac mini (frame memory step 3).

One call does the chain measured in the bake-off (docs/plans/FRAME_MEMORY.md):
  1. kino_memory /search   EmbeddingGemma 2: the frames that look most like the words (one per place)
  2. OWLv2 (CPU)           the top boxes for the words in each of those frames
  3. kino_memory /verify   EmbeddingGemma 2 on each box's crop: margin over unrelated photos
  4. decide                margin >= ACCEPT -> yes; MAYBE..ACCEPT -> Gemma 4 sees a close-up, yes/no;
                           below MAYBE -> no (next box / next frame)
Measured on the owner's room (2026-11-20): every accepted box on the right thing, 0/16 made up.

HTTP, port 8095:
  POST /find {"words": "a helmet", "frames": 6, "enough": 2}
    -> {"ok", "words", "found": [ {frame id, stamp_ns, age_s, box [x0,y0,x1,y1] px, margin, decided_by,
                                   frame_score, room, base_odom} ... newest first ],
        "checked": [...every frame looked at, and why it was not used...], "ms"}
  GET /status

The position is NOT computed here: the caller (the Pi brain) asks the Jetson's pixel_to_goal with the
frame's stamp and the box -- the frame's own depth and pose (step 4).

OWLv2 costs ~0.9 GB: loaded on the first /find, released after IDLE_UNLOAD_S. On the Mac's GPU it pushed
Gemma 4 into swap (replies up to 2x slower, 2026-11-21); on the CPU Gemma was unchanged: CPU it is.
"""
import base64
import gc
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

MEM = os.environ.get('KFIND_MEMORY', 'http://127.0.0.1:8094')
GEMMA = os.environ.get('KFIND_GEMMA', 'http://127.0.0.1:8080')
PORT = int(os.environ.get('KFIND_PORT', '8095'))
ACCEPT, MAYBE = 0.15, 0.06
OWL_TOP = 4                 # boxes per frame checked by EmbeddingGemma 2
MAX_GEMMA = 4               # close-up questions per /find (each ~3-4 s)
GIVE_UP_AFTER = 3           # frames in a row with no box even in the unsure band: stop
COLOURS = re.compile(r'\b(red|orange|yellow|green|blue|purple|violet|pink|brown|black|white|grey|gray|silver|golden|gold|steel|transparent|dark|light)\b', re.I)
IDLE_UNLOAD_S = 180.0
OWL_NAME = 'google/owlv2-base-patch16-ensemble'

_owl, _owl_used, _owl_lock = None, 0.0, threading.Lock()
stats = {'finds': 0, 'gemma_asked': 0, 'owl_loads': 0}


def _get(path):
    return json.load(urllib.request.urlopen(MEM + path, timeout=60))


def _post(url, body, timeout=120):
    r = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def owl():
    global _owl, _owl_used
    with _owl_lock:
        if _owl is None:
            import torch  # noqa: F401
            from transformers import Owlv2ForObjectDetection, Owlv2Processor
            _owl = (Owlv2Processor.from_pretrained(OWL_NAME), Owlv2ForObjectDetection.from_pretrained(OWL_NAME).eval())
            stats['owl_loads'] += 1
        _owl_used = time.time()
        return _owl


def unloader():
    global _owl
    while True:
        time.sleep(30)
        with _owl_lock:
            if _owl is not None and time.time() - _owl_used > IDLE_UNLOAD_S:
                _owl = None
                gc.collect()


def owl_boxes(im, words, top):
    import torch
    proc, model = owl()
    inp = proc(text=[[words]], images=im, return_tensors='pt')
    with torch.no_grad():
        o = model(**inp)
    S = max(im.size)
    r = proc.post_process_grounded_object_detection(o, threshold=0.0, target_sizes=torch.tensor([[S, S]]))[0]
    W, H = im.size
    out = []
    for i in r['scores'].argsort(descending=True):
        b = [float(v) for v in r['boxes'][i]]
        b = [max(0.0, b[0]), max(0.0, b[1]), min(W, b[2]), min(H, b[3])]
        if b[2] - b[0] < 4 or b[3] - b[1] < 4:
            continue
        if any(sum(abs(b[k] - p[k]) for k in range(4)) < 20 for p, _ in out):
            continue
        out.append((b, float(r['scores'][i])))
        if len(out) >= top:
            break
    return out


def gemma_yes(im, box, words):
    """Gemma 4 on a close-up: is it the thing (kind AND colour)? -- only for the unsure band."""
    W, H = im.size
    x0, y0, x1, y1 = box
    side = max(max(x1 - x0, y1 - y0) * 1.6, 96)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    c = im.crop((max(0, cx - side / 2), max(0, cy - side / 2), min(W, cx + side / 2), min(H, cy + side / 2)))
    c = c.resize((384, 384))
    b = io.BytesIO()
    c.save(b, 'JPEG', quality=92)
    body = {'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(b.getvalue()).decode()}},
        {'type': 'text', 'text': (f'Is the main thing in this close-up {words}? '
                                  + ('Its colour must match too. ' if COLOURS.search(words) else
                                     'Judge only what kind of thing it is, not its colour or contents. ')
                                  + 'Answer only "yes" or "no".')}]}],
            'max_tokens': 5, 'temperature': 0}
    stats['gemma_asked'] += 1
    a = _post(GEMMA + '/v1/chat/completions', body)['choices'][0]['message']['content'].strip().lower()
    return a.startswith('yes'), a


def gemma_name(im, box):
    """Gemma 4 names the thing in a close-up (2-5 words, with its colour): for an honest near miss."""
    W, H = im.size
    x0, y0, x1, y1 = box
    side = max(max(x1 - x0, y1 - y0) * 1.6, 96)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    c = im.crop((max(0, cx - side / 2), max(0, cy - side / 2), min(W, cx + side / 2), min(H, cy + side / 2)))
    b = io.BytesIO()
    c.resize((384, 384)).save(b, 'JPEG', quality=92)
    body = {'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(b.getvalue()).decode()}},
        {'type': 'text', 'text': 'Name the main thing in this close-up in 2 to 5 words, with its colour. Only the name.'}]}],
            'max_tokens': 16, 'temperature': 0}
    stats['gemma_asked'] += 1
    return _post(GEMMA + '/v1/chat/completions', body)['choices'][0]['message']['content'].strip().strip('.').lower()


def find(words, n_frames=6, enough=2):
    t0 = time.time()
    stats['finds'] += 1
    words = re.sub(r'\s+', ' ', words.strip())
    hits = _get('/search?' + urllib.parse.urlencode({'q': words, 'k': n_frames}))['hits']
    # "Where is it?" means where it was LAST seen: check the best-matching frames NEWEST first, so the
    # first one confirmed is the latest sighting (live 2026-11-21: best-match-first sent the robot to a
    # 6 h old view although the 07:43 frames showed it; it had been moved 0.6 m).
    hits.sort(key=lambda h: h['stamp_ns'], reverse=True)
    found, checked = [], []
    gemma_left, weak_run = [MAX_GEMMA], [0]
    near = None                                  # best candidate Gemma said no to: (margin, frame hit, box, im)
    for h in hits:
        if len(found) >= enough:
            break
        if h['margin'] < MAYBE / 2:                  # the frame as a whole hardly matches: skip it
            checked.append({'id': h['id'], 'why': 'frame_match_too_weak', 'frame_margin': h['margin']})
            continue
        jpeg = urllib.request.urlopen(f"{MEM}/frame/{h['id']}.jpg", timeout=30).read()
        im = Image.open(io.BytesIO(jpeg)).convert('RGB')
        scored = []
        for box, owl_score in owl_boxes(im, words, OWL_TOP):
            m = _post(MEM + '/verify', {'id': h['id'], 'box': box, 'words': words})['margin']
            scored.append((m, box, owl_score))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            checked.append({'id': h['id'], 'why': 'no_boxes'})
            continue
        pick, why_not = None, None
        if scored[0][0] >= ACCEPT:
            pick, by = scored[0], 'embedding'
        else:
            for cand in [c for c in scored if c[0] >= MAYBE][:2]:      # the best two unsure boxes
                if gemma_left[0] <= 0:
                    why_not = 'gemma_budget_used'
                    break
                gemma_left[0] -= 1
                yes, ans = gemma_yes(im, cand[1], words)
                if yes:
                    pick, by = cand, 'gemma_closeup'
                    break
                why_not = f'gemma_said_{ans[:10]}'
                if near is None or cand[0] > near[0]:
                    near = (cand[0], h, cand[1], im)
        if pick is None:
            weak = scored[0][0] < MAYBE
            weak_run[0] = weak_run[0] + 1 if weak else 0
            checked.append({'id': h['id'], 'why': 'box_match_too_weak' if weak else why_not,
                            'margin': round(scored[0][0], 3)})
            if weak_run[0] >= GIVE_UP_AFTER:
                checked.append({'why': f'gave_up_after_{GIVE_UP_AFTER}_weak_frames'})
                break
            continue
        weak_run[0] = 0
        m, box, owl_score = pick
        found.append({'id': h['id'], 'stamp_ns': h['stamp_ns'], 'age_s': h['age_s'], 'room': h.get('room'),
                      'base_odom': h.get('base_odom'), 'box': [round(v) for v in box], 'margin': round(m, 3),
                      'owl': round(owl_score, 3), 'decided_by': by, 'frame_score': h['score']})
        checked.append({'id': h['id'], 'why': 'found', 'margin': round(m, 3)})
    found.sort(key=lambda f: f['stamp_ns'], reverse=True)          # the newest sighting first
    maybe = None
    if not found and near is not None:
        m, h, box, im = near
        maybe = {'id': h['id'], 'stamp_ns': h['stamp_ns'], 'age_s': h['age_s'], 'room': h.get('room'),
                 'base_odom': h.get('base_odom'), 'box': [round(v) for v in box], 'margin': round(m, 3),
                 'looks_like': gemma_name(im, box)}
    return {'ok': True, 'words': words, 'found': found, 'maybe': maybe, 'checked': checked,
            'ms': round((time.time() - t0) * 1000)}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        d = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith('/status'):
            return self._send(200, {**stats, 'owl_loaded': _owl is not None})
        self._send(404, {'ok': False})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if self.path.startswith('/find'):
                return self._send(200, find(body['words'], int(body.get('frames', 6)), int(body.get('enough', 2))))
            self._send(404, {'ok': False})
        except Exception as e:
            self._send(500, {'ok': False, 'reason': f'{type(e).__name__}: {e}'})


if __name__ == '__main__':
    threading.Thread(target=unloader, daemon=True).start()
    print(f'[kino_find] :{PORT}, memory {MEM}, gemma {GEMMA}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
