#!/usr/bin/env python3
"""kino_memory -- the robot's frame memory on the Mac mini (frame memory step 2, docs/plans/FRAME_MEMORY.md).

Every keyframe the Jetson keeps (frame_capture) is sent here (frame_uploader): its JPEG and its metadata
(stamp, camera pose at the stamp, map <- odom, room). Its depth stays on the Jetson. Each frame is embedded
with EmbeddingGemma 2 (llama-server, CPU, port 8093) as the whole frame + 4 overlapping quarters, 768 numbers
each, so words find frames by meaning ("a helmet", "something to sit on").

HTTP (LAN), port 8094:
  POST /ingest   {"meta": {...frame_capture json...}, "jpeg": "<base64>"}  -> {"ok": true, "id"}   (idempotent)
  GET  /search?q=<words>&k=10                                               -> best frames, one per place
       each hit: id, score, margin (score over ~40 unrelated photos: how much more than chance), tile,
       age_s, stamp_ns, origin, room, base_odom, cam_odom, map_odom, K
  POST /verify   {"id": <frame id> | "jpeg": "<b64>", "box": [x0,y0,x1,y1] (px), "words": "..."}
                 -> {"margin"}: does this crop really show the words? (accept >= 0.15, ask Gemma 0.06-0.15)
  GET  /frame/<id>.jpg
  GET  /status

Measured before building (bake-off 2026-11-20/21): search top-1 15/17, top-3 16/17, 0/8 false on the owner's
room; on this Mac's CPU 0.45 s per image, 17 ms per query, 564 MB; Gemma 4's replies unchanged while it
embeds flat out (photo 1.86 -> 1.95 s, text 2.36 -> 2.34 s).

Store (~/kino-memory/store): frames/<id>.jpg, meta.jsonl (one line per embedded frame), vecs.npy (N x 5 x 768,
float16), reference.npy. Pure stdlib + numpy + Pillow.
"""
import base64
import glob
import io
import json
import os
import queue
import statistics
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

HOME = os.path.expanduser(os.environ.get('KMEM_HOME', '~/kino-memory'))
STORE = os.path.join(HOME, 'store')
EG2 = os.environ.get('KMEM_EG2', 'http://127.0.0.1:8093')
PORT = int(os.environ.get('KMEM_PORT', '8094'))
SPREAD_M, SPREAD_DEG, SPREAD_S = 0.30, 15.0, 4.0    # one hit per place and moment
NEWEST_TOL = 0.02                                   # within this of a place's best score, the newest frame wins
QUERY_PREFIX = 'task: search result | query: '

os.makedirs(os.path.join(STORE, 'frames'), exist_ok=True)
lock = threading.RLock()
work = queue.Queue()
stats = {'embedded': 0, 'queued': 0, 'errors': 0, 'embed_ms': []}


# ── EmbeddingGemma 2 ─────────────────────────────────────────────────────────
def _post(content):
    r = urllib.request.Request(EG2 + '/embeddings', json.dumps({'content': content}).encode(),
                               {'Content-Type': 'application/json'})
    e = json.load(urllib.request.urlopen(r, timeout=120))[0]['embedding']
    v = np.asarray(e[-1] if isinstance(e[0], list) else e, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


_mark = None


def img_vec(im: Image.Image) -> np.ndarray:
    global _mark
    if _mark is None:
        _mark = json.load(urllib.request.urlopen(EG2 + '/props', timeout=10))['media_marker']
    im = im.convert('RGB')
    im.thumbnail((448, 448))
    if min(im.size) < 224:                      # 70 image tokens need >= ~224 px a side
        f = 224 / min(im.size)
        im = im.resize((round(im.size[0] * f), round(im.size[1] * f)))
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=90)
    return _post({'prompt_string': _mark, 'multimodal_data': [base64.b64encode(b.getvalue()).decode()]})


def text_vec(words: str) -> np.ndarray:
    return _post(QUERY_PREFIX + words)


def tiles(im: Image.Image):
    """whole frame + 4 overlapping quarters (as measured in the bake-off)."""
    w, h = im.size
    out = [im]
    for x0, y0 in ((0, 0), (w // 2 - w // 8, 0), (0, h // 2 - h // 8), (w // 2 - w // 8, h // 2 - h // 8)):
        out.append(im.crop((x0, y0, x0 + w // 2 + w // 8, y0 + h // 2 + h // 8)))
    return out


def crop(im: Image.Image, box, pad=0.15):
    W, H = im.size
    x0, y0, x1, y1 = box
    side = max(max(x1 - x0, y1 - y0) * (1 + 2 * pad), 48)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return im.crop((max(0, cx - side / 2), max(0, cy - side / 2), min(W, cx + side / 2), min(H, cy + side / 2)))


# ── the store ────────────────────────────────────────────────────────────────
metas, vecs, ids = [], np.zeros((0, 5, 768), np.float16), set()
refs = None


def load():
    global metas, vecs, ids, refs
    mp, vp = os.path.join(STORE, 'meta.jsonl'), os.path.join(STORE, 'vecs.npy')
    if os.path.exists(mp) and os.path.exists(vp):
        metas = [json.loads(l) for l in open(mp)]
        vecs = np.load(vp)
        n = min(len(metas), len(vecs))            # a crash between the two writes: trust the shorter
        metas, vecs = metas[:n], vecs[:n]
    ids = {m['id'] for m in metas}
    rp = os.path.join(STORE, 'reference.npy')
    if os.path.exists(rp):
        refs = np.load(rp)
    else:
        files = sorted(glob.glob(os.path.join(HOME, 'reference', '*.jpg')))
        if files:
            refs = np.stack([img_vec(Image.open(f)) for f in files])
            np.save(rp, refs)
    print(f'[kino_memory] {len(metas)} frames, {0 if refs is None else len(refs)} reference photos', flush=True)


def add(meta, v5):
    global vecs
    with lock:
        metas.append(meta)
        vecs = np.concatenate([vecs, v5[None].astype(np.float16)])
        with open(os.path.join(STORE, 'meta.jsonl'), 'a') as f:
            f.write(json.dumps(meta) + '\n')
        tmp = os.path.join(STORE, 'vecs.tmp.npy')
        np.save(tmp, vecs)
        os.replace(tmp, os.path.join(STORE, 'vecs.npy'))


def worker():
    while True:
        meta = work.get()
        try:
            t = time.time()
            im = Image.open(os.path.join(STORE, 'frames', f"{meta['id']}.jpg"))
            v5 = np.stack([img_vec(p) for p in tiles(im.convert('RGB'))])
            add(meta, v5)
            stats['embedded'] += 1
            stats['embed_ms'] = (stats['embed_ms'] + [(time.time() - t) * 1000])[-50:]
        except Exception as e:
            stats['errors'] += 1
            print(f'[kino_memory] embed {meta.get("id")} failed: {e}', flush=True)
        finally:
            with lock:
                stats['queued'] = work.qsize()


def ingest(body):
    meta = body['meta']
    fid = int(meta['id'])
    meta['id'] = fid
    with lock:
        if fid in ids:
            return {'ok': True, 'id': fid, 'dup': True}
        ids.add(fid)
    with open(os.path.join(STORE, 'frames', f'{fid}.jpg'), 'wb') as f:
        f.write(base64.b64decode(body['jpeg']))
    work.put(meta)
    stats['queued'] = work.qsize()
    return {'ok': True, 'id': fid}


def _place(m):
    b = m.get('base_odom') or {}
    return m.get('origin'), b.get('x', 0.0), b.get('y', 0.0), b.get('yaw_deg', 0.0), m['stamp_ns'] / 1e9


def search(words, k=10):
    if not len(metas):
        return {'ok': True, 'hits': [], 'frames': 0}
    t = time.time()
    q = text_vec(words)
    with lock:
        V = vecs.astype(np.float32)
        M = list(metas)
    s = V @ q                                     # frames x 5
    best_tile = s.argmax(axis=1)
    score = s.max(axis=1)
    base = float((refs @ q).mean()) if refs is not None else 0.0
    hits, taken = [], []
    now = time.time()
    places = [_place(m) for m in M]

    def same_place(a, b):
        return a[0] == b[0] and np.hypot(a[1] - b[1], a[2] - b[2]) < SPREAD_M \
            and abs((a[3] - b[3] + 180) % 360 - 180) < SPREAD_DEG

    for i in np.argsort(-score):
        if any(same_place(places[i], p) or abs(places[i][4] - p[4]) < SPREAD_S for p in taken):
            continue
        # Things move: of the frames from this same place that match about as well, show the NEWEST
        # (a parked robot keeps many; the best-scoring one may be from before the thing was moved).
        twins = [j for j in range(len(M)) if same_place(places[j], places[i]) and score[j] >= score[i] - NEWEST_TOL]
        i = max(twins, key=lambda j: places[j][4])
        taken.append(places[i])
        m = M[i]
        hits.append({'id': m['id'], 'score': round(float(score[i]), 4), 'margin': round(float(score[i]) - base, 4),
                     'tile': ['whole', 'top-left', 'top-right', 'bottom-left', 'bottom-right'][int(best_tile[i])],
                     'age_s': round(now - places[i][4], 1), 'stamp_ns': m['stamp_ns'], 'origin': m.get('origin'),
                     'room': m.get('room'), 'base_odom': m.get('base_odom'), 'cam_odom': m.get('cam_odom'),
                     'map_odom': m.get('map_odom'), 'K': m.get('K'), 'trigger': m.get('trigger')})
        if len(hits) >= k:
            break
    return {'ok': True, 'query': words, 'frames': len(M), 'baseline': round(base, 4),
            'ms': round((time.time() - t) * 1000), 'hits': hits}


def verify(body):
    if 'jpeg' in body:
        im = Image.open(io.BytesIO(base64.b64decode(body['jpeg']))).convert('RGB')
    else:
        im = Image.open(os.path.join(STORE, 'frames', f"{int(body['id'])}.jpg")).convert('RGB')
    q = text_vec(body['words'])
    s = float(img_vec(crop(im, body['box'])) @ q)
    base = float((refs @ q).mean()) if refs is not None else 0.0
    return {'ok': True, 'score': round(s, 4), 'margin': round(s - base, 4)}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj=None, raw=None, ctype='application/json'):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == '/search':
                p = parse_qs(u.query)
                return self._send(200, search(p['q'][0], int(p.get('k', ['10'])[0])))
            if u.path.startswith('/frame/') and u.path.endswith('.jpg'):
                fid = int(u.path[len('/frame/'):-4])
                return self._send(200, raw=open(os.path.join(STORE, 'frames', f'{fid}.jpg'), 'rb').read(),
                                  ctype='image/jpeg')
            if u.path == '/status':
                ms = stats['embed_ms']
                return self._send(200, {'frames': len(metas), 'queued': work.qsize(), 'embedded': stats['embedded'],
                                        'errors': stats['errors'], 'refs': 0 if refs is None else len(refs),
                                        'embed_ms_median': round(statistics.median(ms)) if ms else None})
            self._send(404, {'ok': False, 'reason': 'unknown path'})
        except Exception as e:
            self._send(500, {'ok': False, 'reason': str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if u.path == '/ingest':
                return self._send(200, ingest(body))
            if u.path == '/verify':
                return self._send(200, verify(body))
            self._send(404, {'ok': False, 'reason': 'unknown path'})
        except Exception as e:
            self._send(500, {'ok': False, 'reason': str(e)})


if __name__ == '__main__':
    load()
    threading.Thread(target=worker, daemon=True).start()
    print(f'[kino_memory] listening on :{PORT}, EmbeddingGemma 2 at {EG2}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
