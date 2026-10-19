"""Music on the Pi 5: "play <song>", pause, resume, next, stop, music volume.

    /audio/music_cmd    String (JSON)  brain -> here
        {"id", "op": "play", "query": "kesariya"}   search, stream the best match,
                                                     then the next results in turn
        {"id", "op": "pause"|"resume"|"stop"|"next"|"status"}
        {"id", "op": "volume", "set": 0-100 | "change": +-N}   the MUSIC level,
                                                     under the speaker volume
    /audio/music_state  String (JSON, latched)  here -> brain
        {"playing", "paused", "title", "query", "volume", "error",
         "last": {"id", "op", "ok", "msg"}}

How it plays: yt-dlp finds the song (YouTube search, audio only, Opus/WebM --
the decoders this Pi has; measured 3.2 s to a stream URL, 2026-10-04) and a
GStreamer playbin streams it to PipeWire's default sink, i.e. whatever speaker
audio_device_node made default. Nothing is downloaded or kept.

Sharing the speaker with the voice:
* the robot talks over the music: /voice/tts_speaking -> the music dips to
  DUCK_FRACTION of its level and comes back after the reply;
* the owner starts a request (/voice/user_input) -> the music dips for the
  turn, so the question and the answer are not fighting it;
* the stop word ("stop", "quiet", ...) -> stt_node publishes "[stop]" on
  /voice/tts_stop -> the music stops. The brain's own "abandon this reply"
  marker on the same topic is NOT a stop.
The Stone stays in HFP (its mic is the robot's ears), so music plays at 8 kHz
mono -- phone quality. docs/voice/ASSISTANT_SCENARIOS.md S9-S13.

Degrades (CLAUDE.md #5): no network, no yt-dlp, no match, a dead stream ->
the reply says why; the node never exits.
"""

import json
import threading
import time
import traceback

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String

try:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
except Exception:                                   # no GStreamer: refuse politely
    Gst = None

try:
    import yt_dlp
except Exception:
    yt_dlp = None

LATCHED = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
STOP_WORD_MARKER = '[stop]'          # stt_node's stop word; anything else on tts_stop is not ours
SEARCH_RESULTS = 5                   # "play X" queues this many matches; "next" walks them
DUCK_FRACTION = 0.25                 # music level while the robot talks / a turn runs
TURN_DUCK_S = 20.0                   # a turn with no reply yet keeps the music down this long
DEFAULT_LEVEL = 60                   # music level (0-100) until the owner changes it
YTDL_OPTS = {'quiet': True, 'no_warnings': True, 'noplaylist': True,
             'format': 'bestaudio[acodec=opus]/bestaudio'}


def level_after(current: int, set_to=None, change=None) -> int:
    """Music level 0..100 after a volume request (pure)."""
    if set_to is not None:
        return max(0, min(100, int(set_to)))
    return max(0, min(100, int(current) + int(change or 0)))


def clean_title(title: str) -> str:
    """Strip the "| Official Video | 4K" tail YouTube titles carry (pure)."""
    t = (title or '').split('|')[0]
    for junk in ('(Official Video)', '(Official Music Video)', '(Lyrics)', '(Audio)',
                 '[Official Video]', '(Official Audio)', 'Official Video'):
        t = t.replace(junk, '')
    return ' '.join(t.split()).strip(' -') or (title or 'the song')


class MediaNode(Node):
    def __init__(self):
        super().__init__('pi5_media')
        self._state_pub = self.create_publisher(String, '/audio/music_state', LATCHED)
        self.create_subscription(String, '/audio/music_cmd', self._on_cmd, 10)
        self.create_subscription(Bool, '/voice/tts_speaking', self._on_speaking, 10)
        self.create_subscription(String, '/voice/tts_stop', self._on_tts_stop, 10)
        self.create_subscription(String, '/voice/user_input', self._on_user_input, 10)

        self._lock = threading.RLock()
        self._player = None
        self._queue: list[dict] = []          # search results still to play
        self._title = ''
        self._query = ''
        self._paused = False
        self._error = ''
        self._level = DEFAULT_LEVEL
        self._speaking = False
        self._turn_until = 0.0
        self._last: dict = {}

        if Gst is None:
            self._error = 'GStreamer is not available'
        elif yt_dlp is None:
            self._error = 'yt-dlp is not installed'
        self.create_timer(0.25, self._poll)
        self._publish()
        self.get_logger().info(f'media up: {self._error or "ready"}')

    # ── requests ───────────────────────────────────────────────────────────

    def _on_cmd(self, msg: String) -> None:
        try:
            cmd = json.loads(msg.data)
            assert isinstance(cmd, dict)
        except Exception:
            self.get_logger().warning(f'/audio/music_cmd: not a JSON object: {msg.data[:80]!r}')
            return
        # play/next resolve over the network (seconds): never in the executor
        threading.Thread(target=self._run_cmd, args=(cmd,), daemon=True).start()

    def _run_cmd(self, cmd: dict) -> None:
        try:
            ok, text = self._handle(cmd)
        except Exception:
            self.get_logger().error(f'music command failed\n{traceback.format_exc()}')
            ok, text = False, 'the music player hit an error'
        self._last = {'id': cmd.get('id'), 'op': cmd.get('op'), 'ok': ok, 'msg': text}
        self.get_logger().info(f"music {cmd.get('op')}: {'ok' if ok else 'refused'} -- {text}")
        self._publish()

    def _handle(self, cmd: dict) -> tuple[bool, str]:
        op = cmd.get('op')
        if op == 'status':
            return True, self._status_text()
        if op == 'volume':
            if cmd.get('set') is None and not cmd.get('change'):
                return True, f'music volume {self._level}%'
            with self._lock:
                self._level = level_after(self._level, cmd.get('set'), cmd.get('change'))
                self._apply_volume()
            return True, f'music volume {self._level}%'
        if op == 'play':
            return self._play_query((cmd.get('query') or '').strip())
        with self._lock:
            if op == 'stop':
                was = self._title
                self._stop()
                self._queue = []
                return True, f'stopped {was}' if was else 'nothing was playing'
            if self._player is None:
                return False, 'nothing is playing'
            if op == 'pause':
                self._player.set_state(Gst.State.PAUSED)
                self._paused = True
                return True, f'paused {self._title}'
            if op == 'resume':
                self._player.set_state(Gst.State.PLAYING)
                self._paused = False
                return True, f'playing {self._title} again'
        if op == 'next':
            return self._play_next()
        return False, f'unknown music op {op!r}'

    def _play_query(self, query: str) -> tuple[bool, str]:
        if self._error and (Gst is None or yt_dlp is None):
            return False, f'I cannot play music: {self._error}'
        if not query:
            return False, 'what should I play?'
        try:
            with yt_dlp.YoutubeDL({**YTDL_OPTS, 'extract_flat': True}) as y:
                found = y.extract_info(f'ytsearch{SEARCH_RESULTS}:{query}', download=False)
        except Exception as e:
            return False, f'I could not search for {query!r} ({type(e).__name__}) -- is the internet up?'
        entries = [e for e in (found or {}).get('entries') or [] if e and e.get('url')]
        if not entries:
            return False, f'I found nothing for {query!r}'
        with self._lock:
            self._query = query
            self._queue = entries
        return self._play_next()

    def _play_next(self) -> tuple[bool, str]:
        while True:
            with self._lock:
                if not self._queue:
                    self._stop()
                    return False, 'no more songs for that search'
                entry = self._queue.pop(0)
            try:
                with yt_dlp.YoutubeDL(YTDL_OPTS) as y:
                    info = y.extract_info(entry['url'], download=False)
                url = info.get('url')
            except Exception as e:
                self.get_logger().warning(f"skipping {entry.get('title')!r}: {type(e).__name__}")
                continue                              # region-blocked, removed: try the next match
            if not url:
                continue
            with self._lock:
                self._start(url, clean_title(info.get('title') or entry.get('title')))
                return True, f'playing {self._title}'

    # ── GStreamer ──────────────────────────────────────────────────────────

    def _start(self, url: str, title: str) -> None:
        self._stop()
        p = Gst.ElementFactory.make('playbin', 'music')
        p.set_property('uri', url)
        p.set_property('video-sink', Gst.ElementFactory.make('fakesink', None))
        self._player, self._title, self._paused, self._error = p, title, False, ''
        self._apply_volume()
        p.set_state(Gst.State.PLAYING)

    def _stop(self) -> None:
        if self._player is not None:
            self._player.set_state(Gst.State.NULL)
        self._player, self._title, self._paused = None, '', False

    def _apply_volume(self) -> None:
        if self._player is None:
            return
        ducked = self._speaking or time.monotonic() < self._turn_until
        level = self._level / 100.0 * (DUCK_FRACTION if ducked else 1.0)
        self._player.set_property('volume', level)

    def _poll(self) -> None:
        """Bus messages (end of song, stream errors) and duck timeouts."""
        with self._lock:
            p = self._player
            if p is None:
                return
            self._apply_volume()
            msg = p.get_bus().pop_filtered(Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if msg is None:
            return
        if msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            self.get_logger().warning(f'stream error on {self._title!r}: {err.message}')
        # end of the song, or a dead stream: the next match, if any
        threading.Thread(target=self._advance, daemon=True).start()

    def _advance(self) -> None:
        ok, text = self._play_next()
        if not ok:
            with self._lock:
                self._stop()
        self._publish()

    # ── sharing the speaker with the voice ─────────────────────────────────

    def _on_speaking(self, msg: Bool) -> None:
        with self._lock:
            self._speaking = bool(msg.data)
            if not self._speaking:
                self._turn_until = 0.0              # the reply is over: come back up
            self._apply_volume()

    def _on_user_input(self, _msg: String) -> None:
        with self._lock:
            self._turn_until = time.monotonic() + TURN_DUCK_S
            self._apply_volume()

    def _on_tts_stop(self, msg: String) -> None:
        if msg.data.strip() != STOP_WORD_MARKER:
            return                                  # the brain abandoning a reply, not "stop"
        with self._lock:
            if self._player is None:
                return
            title = self._title
            self._stop()
            self._queue = []
        self._last = {'id': None, 'op': 'stop', 'ok': True, 'msg': f'stopped {title} (stop word)'}
        self.get_logger().info(f'stop word: stopped {title!r}')
        self._publish()

    # ── state ──────────────────────────────────────────────────────────────

    def _status_text(self) -> str:
        if self._player is None:
            return 'nothing is playing'
        return f"{'paused' if self._paused else 'playing'} {self._title}" + (
            f" ({len(self._queue)} more for {self._query!r})" if self._queue else '')

    def _publish(self) -> None:
        with self._lock:
            state = {'playing': self._player is not None and not self._paused,
                     'paused': self._paused, 'title': self._title, 'query': self._query,
                     'volume': self._level, 'error': self._error, 'last': self._last}
        self._state_pub.publish(String(data=json.dumps(state)))


def main():
    rclpy.init()
    node = MediaNode()
    try:
        rclpy.spin(node)
    finally:
        node._stop()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
