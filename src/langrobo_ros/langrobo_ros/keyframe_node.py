#!/usr/bin/env python3
"""keyframe_node -- the last 10 minutes of what the robot saw, searchable by words.

RUNS ON THE PI 5 since 2026-10-29 (it was built on the Jetson the same day as
~/rover/phase4/nodes/keyframe_memory.py -- same topics, same contract). On the
loaded real robot the Jetson could not hold the model: ~1.8 GB, free RAM 2.2 ->
0.86 GB, load 29 -> 33-39, /odom read 0 Hz in 2 of 6 samples. So now:
  this node (Pi 5, light: pick keyframes, cut quarters, search)
  -> EmbeddingGemma 2 on the Mac mini (scripts/mac_mini/e2mem, :8093)
  -> the depth of each keyframe stays on the Jetson (pixel_to_goal's keyframe store,
     asked for over /vision/keyframe_snapshot within its 2 s depth ring).
The brain asks it through ROS2Bridge.keyframe_query (tools/photo_recall.py).

"Go near the fridge" after driving through the kitchen: the brain should not
have to search. This node keeps a KEYFRAME every time the view has changed
(moved 40 cm, turned 20 deg, or 30 s still), embeds it once with EmbeddingGemma 2
(phase4/tools/embedder.sh, host GPU, 127.0.0.1:8097), and answers a text query
with the frames that match best -- each with its camera stamp, so the Pi 5 can
have pixel_to_goal place the object with THAT frame's depth and pose.

    camera JPEG (image_bridge, 4 Hz) + /odom
        │ changed view?
        ├──▶ /vision/keyframe_snapshot  (stamp)  ──▶ pixel_to_goal keeps its depth + pose
        └──▶ EmbeddingGemma 2: whole frame + 4 quarters ──▶ ring, oldest > 10 min dropped
    "a fridge" ──▶ EmbeddingGemma 2 (text, ~40 ms) ──▶ dot products (< 1 ms) ──▶ best frames

THE MODEL KEEPS NOTHING. Each frame is embedded once on arrival and its 5 x 768
numbers are kept here; forgetting a frame is deleting its row. Nothing else is
recomputed (FRAME_MEMORY.md "why not a cache in the model").

CONTRACT
    in   /camera/color/image_raw/compressed   sensor_msgs/CompressedImage (image_bridge;
                                              header.stamp = the camera's own stamp)
    in   /odom                                nav_msgs/Odometry (fusion2) -- the robot's pose
    in   /fusion/status                       std_msgs/String JSON; a new origin_epoch
                                              (odom restarted) empties the memory: old
                                              positions would point at nothing
    out  /vision/keyframe_snapshot            geometry_msgs/PointStamped, header.stamp =
                                              the keyframe's camera stamp (point unused).
                                              pixel_to_goal keeps that frame's depth + camera
                                              pose for KEYFRAME_MAX_S, so a later
                                              /vision/pixel_query with that stamp + a box
                                              places the object exactly as for a photo.
    in   /vision/keyframe_query               std_msgs/String JSON
                                              {"id": "...", "text": "a fridge", "k": 3,
                                               "jpeg": true}
    out  /vision/keyframe_result              std_msgs/String JSON
         {"id", "ok": true, "query", "frames": N, "window_s", "baseline", "baseline_from",
          "embed_ms", "hits": [
            {"stamp": {"sec", "nanosec"}, "stamp_ns", "age_s",
             "score", "margin", "confidence": "strong"|"possible"|"weak",
             "tile": "whole"|"top-left"|..., "tile_box": [x0, y0, x1, y1] (colour px),
             "robot": {"x", "y", "yaw"} (odom, where the robot was),
             "width", "height", "jpeg": "<base64>"}]}
         or {"id", "ok": false, "reason": "embedder_down" | "empty_text" | ...}
    out  /vision/keyframes/status             std_msgs/String JSON, 1 Hz (the ./rover memory gate)

READING A HIT. "confidence" is the frame's margin over how well the SAME words
match ~40 unrelated photos (models/embed/reference): measured on a webcam,
2026-10-27, a thing in view +0.08..+0.16, absent things +0.02..+0.03. It finds
candidates; it does not decide. The brain's VLM looks at the hit's JPEG and
boxes the object, and the arrival check looks again on the spot.

Hits are spread out: one per moment (4 s) and per place (0.3 m / 15 deg), so
"a chair" can return the bedroom chair AND the hall chair, not five frames of one.
"""

import os

from dotenv import load_dotenv

# The same .env as agent_node (LANGROBO_EMBED_URL, ...), read before the settings below.
load_dotenv(os.path.expanduser(os.getenv("LANGROBO_ENV_FILE", "~/ros2_ws/.env")))

import base64
import json
import math
import os
import queue
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import PointStamped
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

EMBED_URL = os.environ.get('LANGROBO_EMBED_URL', 'http://singireddys-mac-mini.local:8093')
EMBED_TOKENS = os.environ.get('LANGROBO_EMBED_TOKENS', '70')   # must match e2mem (reference cache key)
WINDOW_S = float(os.environ.get('KEYFRAME_WINDOW_S', '600'))
MOVE_M = 0.40             # a new keyframe after moving this far ...
TURN_DEG = 20.0           # ... or turning this much (18 per full rotation) ...
REFRESH_S = 30.0          # ... or this long standing still, so a still scene is not forgotten
MIN_GAP_S = 0.4           # never two keyframes closer than this
SAME_VIEW_SIM = 0.97      # a refresh whose meaning has not changed is not kept
ODOM_SILENT_EXIT_S = 60.0   # camera frames but no /odom this long: restart (see _tick)
QUEUE_MAX = 6             # keyframes waiting to be embedded; past this the oldest is dropped
TIME_GAP_S = 4.0          # hits: one per moment ...
PLACE_GAP_M = 0.30        # ... and one per place
PLACE_GAP_DEG = 15.0
STRONG = 0.07             # margin over the "nothing here" level
POSSIBLE = 0.045
QUERY_PREFIX = 'task: search result | query: '   # EmbeddingGemma's query prompt; images take none
TILE_NAMES = ('whole', 'top-left', 'top-right', 'bottom-left', 'bottom-right')
_STATE = Path(os.environ.get('LANGROBO_STATE_DIR', str(Path.home() / '.langrobo')))
REF_DIR = Path(os.environ.get('LANGROBO_EMBED_REF_DIR', str(_STATE / 'embed_reference')))
REF_CACHE = _STATE / 'embed_reference.npz'
REF_MAX = 60
DIM = 768


def _yaw(q) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def _ang(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def tiles_of(img: np.ndarray) -> list:
    """Whole frame + 4 quarters (a small thing fills more of a quarter)."""
    h, w = img.shape[:2]
    h2, w2 = h // 2, w // 2
    return [img, img[:h2, :w2], img[:h2, w2:], img[h2:, :w2], img[h2:, w2:]]


def tile_boxes(w: int, h: int) -> list:
    w2, h2 = w // 2, h // 2
    return [[0, 0, w, h], [0, 0, w2, h2], [w2, 0, w, h2], [0, h2, w2, h], [w2, h2, w, h]]


def _jpeg(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else b''


class Embedder:
    """EmbeddingGemma 2 behind llama-server. Its image marker is random per
    server start, so it is read from /props and re-read after a restart."""

    def __init__(self, url: str):
        self.url = url
        self.marker = None

    def _call(self, path: str, body=None, timeout=20.0):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data, {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def _embed(self, content) -> np.ndarray:
        try:
            out = self._call('/embeddings', {'content': content})
        except urllib.error.HTTPError as e:
            if e.code == 500:
                self.marker = None            # server restarted: new marker
            raise
        vecs = []
        for item in out:
            e = np.asarray(item['embedding'], dtype=np.float32).reshape(-1, DIM)[-1]
            vecs.append(e / (np.linalg.norm(e) + 1e-9))
        return np.stack(vecs)

    def images(self, jpegs: list) -> np.ndarray:
        if self.marker is None:
            self.marker = self._call('/props', timeout=5)['media_marker']
        return self._embed([{'prompt_string': self.marker,
                             'multimodal_data': [base64.b64encode(j).decode()]} for j in jpegs])

    def text(self, q: str) -> np.ndarray:
        return self._embed(QUERY_PREFIX + q)[0]

    def healthy(self) -> bool:
        try:
            urllib.request.urlopen(self.url + '/health', timeout=2).read()
            return True
        except Exception:
            return False


@dataclass
class Keyframe:
    stamp_ns: int
    sec: int
    nanosec: int
    wall: float
    x: float
    y: float
    yaw: float
    jpeg: bytes
    width: int
    height: int
    vecs: np.ndarray          # (5, 768)


class KeyframeMemory(Node):

    def __init__(self):
        super().__init__('keyframe_memory')
        self.embedder = Embedder(EMBED_URL)
        self.embedder_ok = self.embedder.healthy()
        self.frames: deque = deque()
        self.lock = threading.Lock()
        self.pending: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self.queries: queue.Queue = queue.Queue(maxsize=8)
        self.pose = None                      # (x, y, yaw) from /odom
        self.origin = None
        self.last_kf = None                   # (x, y, yaw, wall) of the last keyframe taken
        self.ref = None                       # (images, 5, 768) reference bank
        self.ref_note = 'building'
        self._started = time.monotonic()
        self._last_odom = 0.0
        self.stats = {'kept': 0, 'same_view': 0, 'dropped_behind': 0, 'skipped_no_embedder': 0,
                      'embed_errors': 0, 'embed_ms': 0.0, 'queries': 0, 'cleared': 0,
                      'images_rx': 0, 'odom_rx': 0}

        self.create_subscription(CompressedImage, '/camera/color/image_raw/compressed', self._on_image, 2)
        self.create_subscription(Odometry, '/odom', self._on_odom, 5)
        self.create_subscription(String, '/fusion/status', self._on_fusion, 2)
        self.create_subscription(String, '/vision/keyframe_query', self._on_query, 5)
        self.snap_pub = self.create_publisher(PointStamped, '/vision/keyframe_snapshot', 10)
        self.result_pub = self.create_publisher(String, '/vision/keyframe_result', 5)
        self.status_pub = self.create_publisher(String, '/vision/keyframes/status', 1)
        self.create_timer(1.0, self._tick)
        self.create_timer(5.0, self._check_embedder)
        threading.Thread(target=self._embed_loop, daemon=True, name='embed').start()
        threading.Thread(target=self._query_loop, daemon=True, name='query').start()
        threading.Thread(target=self._build_reference, daemon=True, name='reference').start()
        self.get_logger().info(
            f'keyframe_memory up: {WINDOW_S:.0f} s window, a keyframe per {MOVE_M} m / {TURN_DEG} deg / '
            f'{REFRESH_S:.0f} s; embedder {EMBED_URL} {"ready" if self.embedder_ok else "NOT reachable"}')

    # ── inputs ──────────────────────────────────────────────────────────────

    def _on_odom(self, msg: Odometry) -> None:
        self.stats['odom_rx'] += 1
        self._last_odom = time.monotonic()
        p = msg.pose.pose
        self.pose = (p.position.x, p.position.y, _yaw(p.orientation))

    def _on_fusion(self, msg: String) -> None:
        try:
            origin = json.loads(msg.data).get('origin_epoch')
        except ValueError:
            return
        if origin is None:
            return
        if self.origin is not None and origin != self.origin:
            with self.lock:
                n = len(self.frames)
                self.frames.clear()
            self.last_kf = None
            self.stats['cleared'] += 1
            self.get_logger().warning(f'odom restarted (origin {self.origin} -> {origin}): forgot {n} keyframes')
        self.origin = origin

    def _on_image(self, msg: CompressedImage) -> None:
        self.stats['images_rx'] += 1
        if self.pose is None:
            self.get_logger().warning('camera frames arrive but no /odom yet -- no keyframes',
                                      throttle_duration_sec=30.0)
            return
        now = time.time()
        x, y, yaw = self.pose
        reason = None
        if self.last_kf is None:
            reason = 'first'
        else:
            lx, ly, lyaw, lt = self.last_kf
            if now - lt < MIN_GAP_S:
                return
            if math.hypot(x - lx, y - ly) >= MOVE_M:
                reason = 'moved'
            elif abs(math.degrees(_ang(yaw - lyaw))) >= TURN_DEG:
                reason = 'turned'
            elif now - lt >= REFRESH_S:
                reason = 'refresh'
        if reason is None:
            return
        self.last_kf = (x, y, yaw, now)
        if not self.embedder_ok:
            self.stats['skipped_no_embedder'] += 1
            return
        # Ask pixel_to_goal to keep this frame's depth NOW: its depth ring is ~2 s.
        snap = PointStamped()
        snap.header.stamp = msg.header.stamp
        snap.header.frame_id = 'keyframe'
        self.snap_pub.publish(snap)
        item = (msg.header.stamp.sec, msg.header.stamp.nanosec, now, (x, y, yaw), bytes(msg.data), reason)
        if self.pending.full():
            try:
                self.pending.get_nowait()
                self.stats['dropped_behind'] += 1
            except queue.Empty:
                pass
        self.pending.put(item)

    # ── embedding (its own thread: the GPU call takes ~0.3 s an image) ─────

    def _embed_loop(self) -> None:
        while True:
            sec, nanosec, wall, (x, y, yaw), data, reason = self.pending.get()
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            try:
                t0 = time.perf_counter()
                vecs = self.embedder.images([_jpeg(t) for t in tiles_of(img)])
                ms = (time.perf_counter() - t0) * 1000
                e = self.stats['embed_ms']
                self.stats['embed_ms'] = round(ms if not e else 0.8 * e + 0.2 * ms, 1)
            except Exception as ex:
                self.stats['embed_errors'] += 1
                self.get_logger().warning(f'embed failed: {ex}', throttle_duration_sec=10.0)
                continue
            self._store(Keyframe(sec * 1_000_000_000 + nanosec, sec, nanosec, wall,
                                 x, y, yaw, data, img.shape[1], img.shape[0], vecs), reason)

    def _store(self, frame: Keyframe, reason: str) -> str:
        """Add a keyframe -> 'kept', or 'replaced' the last one."""
        with self.lock:
            last = self.frames[-1] if self.frames else None
            if (reason == 'refresh' and last is not None
                    and float(last.vecs[0] @ frame.vecs[0]) >= SAME_VIEW_SIM):
                # Parked, same view: REPLACE the last frame with this one (its depth was
                # just kept too). Skipping it instead let the only frame of the room the
                # robot is looking at age out after 10 min (found live 2026-10-29: 1 frame
                # at 599.7 s, 174 refreshes skipped).
                self.frames[-1] = frame
                self.stats['same_view'] += 1
                return 'replaced'
            self.frames.append(frame)
            self.stats['kept'] += 1
            self._expire(time.time())
            return 'kept'

    def _expire(self, now: float) -> None:
        while self.frames and now - self.frames[0].wall > WINDOW_S:
            self.frames.popleft()

    # ── "nothing here" level: the same words against unrelated photos ──────

    def _build_reference(self) -> None:
        files = sorted(p for p in REF_DIR.glob('*.jp*g'))[:REF_MAX]
        key = f'{EMBED_TOKENS}:{len(files)}:' + ','.join(p.name for p in files)
        try:
            if REF_CACHE.exists():
                z = np.load(REF_CACHE, allow_pickle=False)
                if str(z['key']) == key:
                    self.ref = z['vecs'].astype(np.float32)
                    self.ref_note = f'{len(self.ref)} photos (cached)'
                    return
            if not files:
                self.ref_note = f'none: no photos in {REF_DIR}'
                return
            while not self.embedder_ok:
                time.sleep(5)
            out = []
            for p in files:
                img = cv2.imread(str(p))
                if img is None:
                    continue
                h, w = img.shape[:2]
                img = cv2.resize(img, (448, max(1, int(h * 448 / w))), interpolation=cv2.INTER_AREA)
                out.append(self.embedder.images([_jpeg(t) for t in tiles_of(img)]))
            self.ref = np.stack(out)
            self.ref_note = f'{len(out)} photos'
            try:
                np.savez(REF_CACHE, vecs=self.ref.astype(np.float16), key=np.array(key))
            except OSError as ex:
                self.get_logger().warning(f'reference not cached ({ex}); rebuilt next start')
            self.get_logger().info(f'reference level ready: {self.ref_note}')
        except Exception as ex:
            self.ref_note = f'failed: {ex}'
            self.get_logger().warning(f'reference build failed: {ex} -- confidence falls back to the other frames')

    # ── queries ─────────────────────────────────────────────────────────────

    def _on_query(self, msg: String) -> None:
        try:
            q = json.loads(msg.data)
        except ValueError:
            q = {'id': '', 'text': msg.data}
        try:
            self.queries.put_nowait(q)
        except queue.Full:
            self._reply(str(q.get('id', '')), False, reason='busy')

    def _reply(self, req_id: str, ok: bool, **fields) -> None:
        self.result_pub.publish(String(data=json.dumps({'id': req_id, 'ok': ok, **fields})))

    def _query_loop(self) -> None:
        while True:
            q = self.queries.get()
            req_id, text = str(q.get('id', '')), str(q.get('text', '')).strip()
            if not text:
                self._reply(req_id, False, reason='empty_text')
                continue
            try:
                t0 = time.perf_counter()
                qv = self.embedder.text(text)
                embed_ms = round((time.perf_counter() - t0) * 1000, 1)
            except Exception as ex:
                self._reply(req_id, False, reason='embedder_down', detail=str(ex)[:160])
                continue
            res = self.search(qv, k=max(1, min(int(q.get('k', 3)), 6)), with_jpeg=bool(q.get('jpeg', True)))
            self.stats['queries'] += 1
            best = res['hits'][0] if res['hits'] else None
            self.get_logger().info(
                f'query "{text}": {res["frames"]} frames, ' + (
                    f'best {best["confidence"]} (margin {best["margin"]:+.3f}, {best["age_s"]:.0f} s ago, '
                    f'{best["tile"]})' if best else 'nothing remembered'))
            self._reply(req_id, True, query=text, embed_ms=embed_ms, **res)

    def search(self, qv: np.ndarray, k: int = 3, with_jpeg: bool = True) -> dict:
        with self.lock:
            self._expire(time.time())
            frames = list(self.frames)
        out = {'frames': len(frames), 'window_s': WINDOW_S, 'hits': []}
        if not frames:
            out.update(baseline=None, baseline_from=None)
            return out
        sims = np.stack([f.vecs @ qv for f in frames])          # (frames, 5)
        score, best_tile = sims.max(axis=1), sims.argmax(axis=1)
        if self.ref is not None:
            baseline = float(np.percentile((self.ref @ qv).max(axis=1), 90))
            out['baseline_from'] = 'reference photos'
        else:
            baseline = float(np.median(score))
            out['baseline_from'] = 'other frames'
        out['baseline'] = round(baseline, 3)
        picked = []
        for i in np.argsort(-score):
            f = frames[int(i)]
            if all(abs(f.wall - frames[j].wall) > TIME_GAP_S and
                   (math.hypot(f.x - frames[j].x, f.y - frames[j].y) > PLACE_GAP_M or
                    abs(math.degrees(_ang(f.yaw - frames[j].yaw))) > PLACE_GAP_DEG) for j in picked):
                picked.append(int(i))
            if len(picked) == k:
                break
        now = time.time()
        for i in picked:
            f, margin = frames[i], float(score[i]) - baseline
            hit = {'stamp': {'sec': f.sec, 'nanosec': f.nanosec}, 'stamp_ns': f.stamp_ns,
                   'age_s': round(now - f.wall, 1), 'score': round(float(score[i]), 3),
                   'margin': round(margin, 3),
                   'confidence': 'strong' if margin >= STRONG else 'possible' if margin >= POSSIBLE else 'weak',
                   'tile': TILE_NAMES[int(best_tile[i])],
                   'tile_box': tile_boxes(f.width, f.height)[int(best_tile[i])],
                   'robot': {'x': round(f.x, 3), 'y': round(f.y, 3), 'yaw': round(f.yaw, 4)},
                   'width': f.width, 'height': f.height}
            if with_jpeg:
                hit['jpeg'] = base64.b64encode(f.jpeg).decode()
            out['hits'].append(hit)
        return out

    # ── health ──────────────────────────────────────────────────────────────

    def _check_embedder(self) -> None:
        ok = self.embedder.healthy()
        if ok != self.embedder_ok:
            self.get_logger().warning(f'embedder {"back" if ok else "DOWN -- keyframes paused"}')
        self.embedder_ok = ok

    def _tick(self) -> None:
        # Self-heal: on 2026-10-30 a freshly started instance got camera frames
        # but never its /odom subscription matched (a fresh process on the same
        # Pi got both), so it kept nothing for minutes. Leave; the launch
        # respawns it with new subscriptions.
        since = time.monotonic() - (self._last_odom or self._started)
        if self.stats['images_rx'] > 0 and since > ODOM_SILENT_EXIT_S:
            self.get_logger().error(f'no /odom for {since:.0f} s while camera frames '
                                    f'arrive -- restarting to re-subscribe')
            os._exit(3)
        with self.lock:
            self._expire(time.time())
            n = len(self.frames)
            oldest = time.time() - self.frames[0].wall if self.frames else None
            mb = sum(len(f.jpeg) + f.vecs.nbytes for f in self.frames) / 1e6
        self.status_pub.publish(String(data=json.dumps({
            'frames': n, 'oldest_s': round(oldest, 1) if oldest is not None else None,
            'window_s': WINDOW_S, 'memory_mb': round(mb, 1), 'embedder': self.embedder_ok,
            'pending': self.pending.qsize(), 'reference': self.ref_note, 'origin_epoch': self.origin,
            **self.stats})))


def main():
    rclpy.init()
    node = KeyframeMemory()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        finally:
            # Its worker threads wait on queues forever; on the Jetson the
            # node outlived SIGINT and SIGTERM (2026-10-29). Leave for sure.
            os._exit(0)


if __name__ == '__main__':
    main()
