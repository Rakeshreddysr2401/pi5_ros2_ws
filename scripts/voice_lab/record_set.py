#!/usr/bin/env python3
"""Record your voice ONCE, so every model can be tested on the same clips.

    systemctl --user stop langrobo-voice                      # free the CPU, no double listening
    python3 scripts/voice_lab/record_set.py --session normal  # from where you usually stand
    python3 scripts/voice_lab/record_set.py --session close   # ~half a metre, facing the mic
    systemctl --user start langrobo-voice

For each sentence: press Enter, say it, press Enter. The audio is saved exactly as
the mic delivers it (16 kHz mono, nothing applied) to
~/voice_lab/recordings/<session>/NN.wav, with the sentence in sentences.tsv.
Re-running a session resumes where you stopped. Your own sentences:
--sentences my_list.txt (one per line).
"""
import argparse
import os
import sys
import threading
import wave

import numpy as np
import sounddevice as sd

SR = 16000
ROOT = os.path.expanduser('~/voice_lab/recordings')

SENTENCES = [
    "Mitra, go to the kitchen.",
    "What do you see?",
    "Stop.",
    "Find the earphones on the floor and go near to it.",
    "What is the time now?",
    "Turn left ninety degrees.",
    "Move forward one meter.",
    "Come here to me.",
    "Follow me.",
    "Save this place as the living room.",
    "Play some music.",
    "Volume up.",
    "Add milk to the shopping list.",
    "Remind me in ten minutes to switch off the stove.",
    "Send a message to dad on Telegram.",
    "Is there anything on the table?",
    "Go to the box and tell me what is on it.",
    "Move back.",
    "How far is the chair from you?",
    "Can you find my dad and say hello to him?",
]


class Recorder:
    """Keeps the mic open the whole time; collects samples only while recording."""

    def __init__(self):
        self._lock = threading.Lock()
        self._on = False
        self._chunks: list = []
        self._stream = sd.InputStream(samplerate=SR, channels=1, dtype='int16',
                                      blocksize=480, callback=self._cb)
        self._stream.start()

    def _cb(self, data, frames, t, status):
        with self._lock:
            if self._on:
                self._chunks.append(data[:, 0].copy())

    def start(self):
        with self._lock:
            self._chunks, self._on = [], True

    def stop(self) -> np.ndarray:
        with self._lock:
            self._on = False
            return np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.int16)


def save(path, pcm):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', required=True, help='e.g. normal, close')
    ap.add_argument('--sentences', help='your own list, one sentence per line')
    a = ap.parse_args()
    sentences = SENTENCES
    if a.sentences:
        sentences = [l.strip() for l in open(a.sentences, encoding='utf8') if l.strip()]

    out = os.path.join(ROOT, a.session)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'sentences.tsv'), 'w', encoding='utf8') as f:
        for i, s in enumerate(sentences, 1):
            f.write(f'{i:02d}.wav\t{s}\n')

    rec = Recorder()
    print(f'\nSession "{a.session}" -> {out}')
    print('For each sentence: Enter = start, say it, Enter = stop.  '
          'Then: Enter = next, r = redo, q = quit.\n')
    i = 0
    while i < len(sentences):
        path = os.path.join(out, f'{i + 1:02d}.wav')
        if os.path.exists(path):
            i += 1
            continue                                   # resume: already recorded
        print(f'[{i + 1}/{len(sentences)}]  "{sentences[i]}"')
        input('    Enter to START... ')
        rec.start()
        input('    recording -- speak, then Enter to STOP ')
        pcm = rec.stop()
        peak = np.abs(pcm.astype(np.float32)).max() / 32768 if len(pcm) else 0.0
        note = ('  <-- CLIPPED, redo a bit softer' if peak > 0.98 else
                '  <-- very quiet, redo a bit louder' if peak < 0.05 else '')
        print(f'    {len(pcm) / SR:.1f} s, peak {peak:.2f}{note}')
        if len(pcm) < SR * 0.3:
            print('    too short -- again\n')
            continue
        save(path, pcm)
        choice = input('    Enter = next | r = redo | q = quit: ').strip().lower()
        if choice == 'r':
            os.remove(path)
        elif choice == 'q':
            break
        else:
            i += 1
        print()
    done = sum(os.path.exists(os.path.join(out, f'{n:02d}.wav')) for n in range(1, len(sentences) + 1))
    print(f'\n{done}/{len(sentences)} recorded in {out}')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nstopped -- run the same command again to continue')
        sys.exit(0)
