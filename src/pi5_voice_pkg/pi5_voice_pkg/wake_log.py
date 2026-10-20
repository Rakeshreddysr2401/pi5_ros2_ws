"""Wake log — one JSON line per transcribed utterance: what was heard, what the
gate decided, and why. Read it with scripts/wake_report.py.

The journal says "not addressed to me — ignored" scattered among heartbeats,
and says nothing at all when the name DID match. To improve the wake word we
need the misses side by side with the hits: "Keno, go to the kitchen" ignored
is a missing alias, not a quiet room. So each line also carries `near`, the
word that sounded most like the name (difflib ratio, no model), and the audio
of every wake and near-miss is kept as a short WAV for listening back or for
training kino.onnx later.

Writes happen on the transcribe worker thread, never the audio callback.
Any failure here is logged once and swallowed: a full disk must not cost the
robot its ears (CLAUDE.md #4).
"""

import difflib
import json
import os
import re
import time
import wave

import numpy as np

from .addressing import sounds

NEAR_MISS_RATIO = 0.9     # on sound keys: "keno"/"chino"/"gino" = "kino"; "kinda" 0.89, "kind" 0.75
MAX_CLIPS = 500           # ~16 kHz mono, a few seconds each: < 100 MB
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def name_words(aliases) -> list[str]:
    """The name from each alias, alone and fused: 'hey kino' -> 'kino', 'heykino'.

    A recogniser often writes "hey kino" as one word ("Ekina, can you go
    forward?" -- 2026-10-20, ignored), so the fused form is a name too.
    """
    words = set()
    for a in aliases or ():
        parts = (a or '').lower().split()
        if parts:
            words.update({parts[-1], ''.join(parts)})
    return sorted(words)


def near_miss(text: str, names) -> tuple[str, float] | None:
    """The word in `text` that sounds most like a name, if it is not the name.

    None when a name is spelled exactly (that is a hit, not a miss) or when
    nothing sounds within NEAR_MISS_RATIO.
    """
    best = None
    keys = {name: sounds(name) for name in names}
    for word in _WORD.findall(text.lower()):
        if len(word) < 3:
            continue
        for name, key in keys.items():
            if word == name:
                return None
            r = difflib.SequenceMatcher(None, sounds(word), key).ratio()
            if r >= NEAR_MISS_RATIO and (best is None or r > best[1]):
                best = (word, round(r, 2))
    return best


class WakeLog:
    def __init__(self, directory: str, aliases, keep_clips: bool = True,
                 max_clips: int = MAX_CLIPS, logger=None):
        self._dir = os.path.expanduser(directory) if directory else ''
        self._clips = os.path.join(self._dir, 'clips')
        self._names = name_words(aliases)
        self._keep_clips = keep_clips
        self._max_clips = max_clips
        self._logger = logger
        self._warned = False
        if self._dir:
            try:
                os.makedirs(self._clips, exist_ok=True)
            except OSError as e:
                self._fail(e)

    @property
    def enabled(self) -> bool:
        return bool(self._dir)

    def record(self, text: str, decision: str, *, pcm=None, sample_rate: int = 16000,
               alias: bool = False, provider: str = '', latency_ms: int = 0, **extra) -> None:
        """Append one line; keep the clip of a wake or a near-miss."""
        if not self._dir:
            return
        try:
            now = time.time()
            near = None if alias else near_miss(text, self._names)
            entry = {
                't': round(now, 2),
                'when': time.strftime('%H:%M:%S', time.localtime(now)),
                'decision': decision,
                'text': text,
                'name_heard': alias,
                'near': {'word': near[0], 'ratio': near[1]} if near else None,
                'provider': provider,
                'latency_ms': latency_ms,
                'audio_s': round(len(pcm) / sample_rate, 2) if pcm is not None else None,
            }
            entry.update(extra)
            if pcm is not None and self._keep_clips and (alias or near):
                entry['clip'] = self._save_clip(pcm, sample_rate, now, decision)
            day = time.strftime('%Y-%m-%d', time.localtime(now))
            with open(os.path.join(self._dir, f'wake-{day}.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:  # noqa: BLE001 -- never let logging take the mic down
            self._fail(e)

    def _save_clip(self, pcm, sample_rate: int, now: float, decision: str) -> str:
        name = time.strftime('%Y%m%d-%H%M%S', time.localtime(now)) + f'-{int(now * 1000) % 1000:03d}-{decision}.wav'
        path = os.path.join(self._clips, name)
        data = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes((data * 32767).astype('<i2').tobytes())
        self._prune()
        return name

    def _prune(self) -> None:
        clips = sorted(f for f in os.listdir(self._clips) if f.endswith('.wav'))
        for old in clips[:max(0, len(clips) - self._max_clips)]:
            os.remove(os.path.join(self._clips, old))

    def _fail(self, e: Exception) -> None:
        if not self._warned and self._logger is not None:
            self._logger.warning(f'wake log disabled for this run ({e})')
        self._warned = True
        self._dir = ''
