#!/usr/bin/env python3
"""Play the owner's recordings into the REAL stt_node and score what it heard.

    python3 scripts/voice_lab/replay_test.py [normal close]          # as configured (Sarvam)
    python3 scripts/voice_lab/replay_test.py --no-sarvam [sessions]  # Sarvam "down" -> fallback

Everything after the microphone is the robot's own code and settings: stt_node
with voice_params.yaml (Silero cutting sentences, the gates, the provider and its
fallback). Only the mic is replaced: a fake input stream delivers each recording in
30 ms frames, in real time, with 1.5 s of silence between sentences. Runs on ROS
domain 77 with no brain and no speaker, so nothing answers and nothing moves.

Reports, per sentence: was it cut as ONE sentence, dropped, or split; which provider
answered; how long it took; and the word errors against what was said.
"""
import argparse
import os
import sys
import threading
import time
import wave

os.environ['ROS_DOMAIN_ID'] = '77'                  # never the robot's domain 0

import numpy as np                                   # noqa: E402

sys.path.insert(0, os.path.expanduser('~/ros2_ws/scripts/voice_lab'))
from score_set import errors                         # noqa: E402

ROOT = os.path.expanduser('~/voice_lab/recordings')
PARAMS = os.path.expanduser(
    '~/ros2_ws/install/pi5_voice_pkg/share/pi5_voice_pkg/config/voice_params.yaml')
SR, FRAME = 16000, 480
GAP_S, MAX_S = 1.5, 30.0


class Playlist:
    """The recordings in order, with silence between; knows which clip is playing."""

    def __init__(self, sessions):
        self.clips, self.current = [], None
        for s in sessions:
            for line in open(os.path.join(ROOT, s, 'sentences.tsv'), encoding='utf8'):
                fn, text = line.rstrip('\n').split('\t')
                path = os.path.join(ROOT, s, fn)
                if not os.path.exists(path):
                    continue
                with wave.open(path) as w:
                    pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
                if len(pcm) / SR > MAX_S:
                    print(f'skipping {s}/{fn} ({len(pcm) / SR:.0f} s -- a mistaken take)')
                    continue
                self.clips.append((f'{s}/{fn}', text, pcm))
        self.done = threading.Event()


def fake_input_stream(playlist):
    """A sounddevice.InputStream stand-in that plays the recordings into the callback."""

    class FakeInputStream:
        def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
            assert samplerate == SR and blocksize == FRAME and dtype == 'int16'
            self._cb, self._stop = callback, threading.Event()

        def start(self):
            threading.Thread(target=self._run, daemon=True).start()

        def _run(self):
            silence = np.zeros(int(GAP_S * SR), np.int16)
            self._t = time.monotonic()
            for idx, (_, _, pcm) in enumerate(playlist.clips):
                # the gap still belongs to the previous sentence: a sentence is
                # cut ~0.6 s AFTER its speech ends, i.e. inside this silence
                if not self._feed(silence):
                    return
                playlist.current = idx
                if not self._feed(pcm):
                    return
            self._feed(silence)
            playlist.done.set()

        def _feed(self, audio) -> bool:
            for i in range(0, len(audio) - FRAME + 1, FRAME):
                if self._stop.is_set():
                    return False
                self._cb(audio[i:i + FRAME].reshape(-1, 1), FRAME, None, None)
                self._t += FRAME / SR
                time.sleep(max(0.0, self._t - time.monotonic()))
            return True

        def stop(self):
            self._stop.set()

        def close(self):
            pass

    return FakeInputStream


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sessions', nargs='*', default=['normal', 'close'])
    ap.add_argument('--no-sarvam', action='store_true', help='pretend Sarvam is down')
    ap.add_argument('--only', nargs='*', default=[], help='just these clips, e.g. normal/09.wav')
    ap.add_argument('--save', help='save each audio the node transcribed into this dir')
    ap.add_argument('--trace', action='store_true', help="print Silero's peak and the cut events per sentence")
    ap.add_argument('--lead', type=float, help="override stt_node's lead-in (s of audio kept "
                    'from before the voice is detected); default: the node\'s own')
    a = ap.parse_args()
    if a.no_sarvam:
        empty = '/tmp/replay_test_empty.env'
        open(empty, 'w').close()
        os.environ['LANGROBO_ENV_FILE'] = empty
        os.environ.pop('SARVAM_API_KEY', None)

    playlist = Playlist(a.sessions)
    if a.only:
        playlist.clips = [c for c in playlist.clips if c[0] in a.only]
    if a.save:
        os.makedirs(a.save, exist_ok=True)
    import rclpy
    from pi5_voice_pkg import stt_node as mod
    mod.sd.InputStream = fake_input_stream(playlist)
    ros_args = ['--ros-args', '--params-file', PARAMS]
    if a.lead is not None:
        ros_args += ['-p', f'lead_in_s:={a.lead}']
    rclpy.init(args=ros_args)
    node = mod.STTNode()
    print(f"lead-in {node.get_parameter('lead_in_s').value:.2f} s", flush=True)
    results = {i: {'heard': [], 'dropped': [], 'provider': set(), 'ms': []}
               for i in range(len(playlist.clips))}
    tags, current = {}, {'clip': None}

    put = node._queue.put_nowait                     # tag each utterance with its clip

    def tagged_put(item):
        tags[id(item[1])] = playlist.current
        put(item)
    node._queue.put_nowait = tagged_put

    transcribe = node._transcribe

    def tagged_transcribe(pcm):
        current['clip'] = tags.pop(id(pcm), None)
        if a.save and current['clip'] is not None:
            name = playlist.clips[current['clip']][0].replace('/', '_')
            with wave.open(os.path.join(a.save, f'{name}.{time.monotonic():.0f}.wav'), 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
        transcribe(pcm)
    node._transcribe = tagged_transcribe

    meta = node._publish_stt_meta

    def record(provider, fell_back, latency_ms, samples, text):
        c = current['clip']
        if c is not None:
            results[c]['heard'].append(text)
            results[c]['provider'].add(provider + (' (fallback)' if fell_back else ''))
            results[c]['ms'].append(latency_ms)
        meta(provider, fell_back, latency_ms, samples, text)
    node._publish_stt_meta = record

    peak = {}
    is_speech = node._vad.is_speech

    def traced(frame, sr):
        v = is_speech(frame, sr)
        c = playlist.current
        if c is not None:
            peak[c] = max(peak.get(c, 0.0), getattr(node._vad, 'prob', float(v)))
        return v
    if a.trace:
        node._vad.is_speech = traced

    vad_pub = node._debug_vad_pub.publish

    def vad(msg):
        if a.trace and playlist.current is not None:
            print(f'    [{playlist.clips[playlist.current][0]}] {msg.data}', flush=True)
        if msg.data.startswith('dropped') and playlist.current is not None:
            results[playlist.current]['dropped'].append(msg.data[9:])
        vad_pub(msg)
    node._debug_vad_pub.publish = vad

    print(f'provider = {node._provider.name}, fallback = {node._fallback.name}; '
          f'playing {len(playlist.clips)} sentences in real time...\n', flush=True)
    node._open_input()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    playlist.done.wait()
    time.sleep(4.0)                                   # let the last transcription finish

    E = N = perfect = one_piece = 0
    rows = []
    for i, (name, text, _) in enumerate(playlist.clips):
        r = results[i]
        heard = ' '.join(t for t in r['heard'] if t)
        e, n = errors(text, heard)
        E += e; N += n; perfect += e == 0
        pieces = len(r['heard'])
        one_piece += pieces == 1
        shape = ('DROPPED: ' + '; '.join(r['dropped'])) if not pieces else (
            'one sentence' if pieces == 1 else f'SPLIT into {pieces}')
        if a.trace:
            shape += f' (silero peak {peak.get(i, 0):.2f})'
        rows.append(f"{'ok ' if e == 0 else 'ERR'} {name}  {shape:<14} "
                    f"{'/'.join(sorted(r['provider'])) or '-':<18} "
                    f"{(str(max(r['ms'])) + ' ms') if r['ms'] else '':>8}  "
                    f"\"{text}\" -> \"{heard}\"")
    print('\n'.join(rows))
    ms = [m for r in results.values() for m in r['ms']]
    print(f'\n{len(playlist.clips)} sentences: {one_piece} cut as exactly one sentence, '
          f'{perfect} perfect, word errors {100 * E / max(N, 1):.1f} %, '
          f'recogniser median {int(np.median(ms)) if ms else 0} ms')
    node._close_input()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
