#!/usr/bin/env python3
"""Score every STT option on the recordings made with record_set.py.

    python3 scripts/voice_lab/score_set.py [normal close ...]     # default: every session

Each clip goes to each model untouched; the text is compared with the sentence you
read (word error rate: lower is better). Clips over 30 s are skipped (a mistaken
take). Writes ~/voice_lab/recordings/results.md and prints the same.

Models: Parakeet 0.6B, Moonshine base, whisper tiny.en, whisper small.en (local, Pi
CPU, 2 threads) and Sarvam saaras:v3 (cloud) twice -- as the robot calls it today
(te-IN, translate) and as plain English (en-IN, transcribe).
"""
import gc
import os
import re
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.expanduser('~/ros2_ws/src/pi5_voice_pkg'))
from dotenv import load_dotenv                                                # noqa: E402

ROOT = os.path.expanduser('~/voice_lab/recordings')
MODELS = os.path.expanduser('~/ros2_ws/src/langrobo_ros/models')
SR, THREADS, MAX_S = 16000, 2, 30.0
WORDS = {'90': 'ninety', '10': 'ten', '1': 'one', 'metre': 'meter', 'metres': 'meters',
         "what's": 'what is', "it's": 'it is', 'ok': 'okay'}


def norm(t: str) -> list[str]:
    t = re.sub(r"[^\w' ]", ' ', t.lower().replace('-', ' '))
    return ' '.join(WORDS.get(w, w) for w in t.split()).split()


def errors(ref: str, hyp: str) -> tuple[int, int]:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


def load_session(name):
    d = os.path.join(ROOT, name)
    clips, skipped = [], []
    for line in open(os.path.join(d, 'sentences.tsv'), encoding='utf8'):
        fn, text = line.rstrip('\n').split('\t')
        path = os.path.join(d, fn)
        if not os.path.exists(path):
            continue
        with wave.open(path) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        if len(pcm) / SR > MAX_S:
            skipped.append(f'{fn} ({len(pcm) / SR:.0f} s)')
            continue
        clips.append((fn, text, pcm, np.abs(pcm).max() > 0.98))
    return clips, skipped


def models():
    """(name, factory) -- built one at a time so only one model is in memory."""
    from pi5_voice_pkg.stt_providers.local_sherpa import LocalSherpaProvider
    from pi5_voice_pkg.stt_providers.local_whisper import LocalWhisperProvider
    from pi5_voice_pkg.stt_providers.sarvam import SarvamProvider
    load_dotenv(os.path.expanduser('~/ros2_ws/.env'))
    key = os.environ.get('SARVAM_API_KEY', '')

    class SarvamEnglish(SarvamProvider):
        def transcribe(self, pcm, sr):
            from pi5_voice_pkg._wav import pcm_to_wav_bytes
            r = self._http.post('https://api.sarvam.ai/speech-to-text',
                                files={'file': ('u.wav', pcm_to_wav_bytes(pcm, sr), 'audio/wav')},
                                data={'model': 'saaras:v3', 'mode': 'transcribe', 'language_code': 'en-IN'},
                                timeout=15)
            r.raise_for_status()
            return r.json().get('transcript', '')

    return [
        ('Parakeet 0.6B (Pi)', lambda: LocalSherpaProvider(
            f'{MODELS}/sherpa/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8', THREADS)),
        ('Moonshine base (Pi)', lambda: LocalSherpaProvider(
            f'{MODELS}/sherpa/sherpa-onnx-moonshine-base-en-int8', THREADS)),
        ('whisper tiny.en (Pi)', lambda: LocalWhisperProvider('tiny.en', f'{MODELS}/whisper', THREADS, beam_size=1)),
        ('whisper small.en (Pi)', lambda: LocalWhisperProvider('small.en', f'{MODELS}/whisper', THREADS, beam_size=1)),
        ('Sarvam, as robot (te->en)', lambda: SarvamProvider(key, 'te-IN')),
        ('Sarvam, English (en-IN)', lambda: SarvamEnglish(key, 'en-IN')),
    ]


def main():
    names = sys.argv[1:] or sorted(n for n in os.listdir(ROOT) if os.path.isdir(os.path.join(ROOT, n)))
    sessions = {n: load_session(n) for n in names}
    for n, (clips, skipped) in sessions.items():
        print(f'{n}: {len(clips)} clips' + (f', skipped {", ".join(skipped)}' if skipped else '')
              + f', {sum(c[3] for c in clips)} hit full scale', flush=True)
    rows, misses = [], {}
    for mname, make in models():
        try:
            m = make()
            m.transcribe(np.zeros(SR, np.float32), SR)
        except Exception as e:
            print(f'{mname}: unavailable ({e})', flush=True)
            continue
        row = [mname]
        for sname, (clips, _) in sessions.items():
            E = N = right = 0
            secs = 0.0
            for fn, text, pcm, _clip in clips:
                t = time.time()
                try:
                    hyp = m.transcribe(pcm, SR)
                except Exception as e:
                    hyp = f'(error {e})'
                secs += time.time() - t
                e_, n_ = errors(text, hyp)
                E += e_; N += n_; right += e_ == 0
                if e_:
                    misses.setdefault((mname, sname), []).append(f'{fn} "{text}" -> "{hyp.strip()}"')
            row.append(f'{100 * E / N:.1f} % ({right}/{len(clips)} perfect)')
            row.append(f'{secs / len(clips):.2f} s')
        rows.append(row)
        print(f'{mname}: ' + ' | '.join(row[1:]), flush=True)
        del m
        gc.collect()

    head = ['model'] + [x for s in sessions for x in (f'{s}: word errors', f'{s}: time/clip')]
    out = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    out += ['| ' + ' | '.join(r) + ' |' for r in rows]
    out.append('\n## Mistakes\n')
    for (mname, sname), lst in misses.items():
        out.append(f'**{mname} — {sname}**')
        out += [f'- {l}' for l in lst]
        out.append('')
    text = '\n'.join(out)
    open(os.path.join(ROOT, 'results.md'), 'w', encoding='utf8').write(text + '\n')
    print('\n' + text)


if __name__ == '__main__':
    main()
