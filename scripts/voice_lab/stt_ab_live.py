#!/usr/bin/env python3
"""Speak once, see every local English STT model transcribe the SAME untouched audio.

    systemctl --user stop langrobo-voice      # fair CPU timing
    python3 scripts/voice_lab/stt_ab_live.py [--end 0.6] [--lead 0.3] [--save]
    systemctl --user start langrobo-voice

Your voice is NOT processed: the mic's samples (16 kHz mono from PipeWire) go to
the models exactly as captured. Silero VAD only decides where a sentence starts
and ends; it never changes the audio.

  --end S    seconds of silence that end a sentence (default 0.6, as the robot)
  --lead S   seconds of audio kept from just before you started (default 0.3)
  --save     keep each sentence as ~/voice_lab/sentences/NN.wav -- exactly what
             the models received (aplay plays it on the Stone)

Models: Parakeet 0.6B, Moonshine base, whisper tiny.en (the old local), 2 threads
each as in the robot. Ctrl+C to quit.
"""
import argparse
import collections
import os
import queue
import sys
import threading
import time
import wave

import numpy as np
import sounddevice as sd

sys.path.insert(0, os.path.expanduser('~/ros2_ws/src/pi5_voice_pkg'))
from pi5_voice_pkg.vad_silero import SileroVad                              # noqa: E402
from pi5_voice_pkg.stt_providers.local_sherpa import LocalSherpaProvider    # noqa: E402
from pi5_voice_pkg.stt_providers.local_whisper import LocalWhisperProvider  # noqa: E402

MODELS = os.path.expanduser('~/ros2_ws/src/langrobo_ros/models')
SAVE_DIR = os.path.expanduser('~/voice_lab/sentences')
SR, FRAME_S = 16000, 0.03
FRAME = int(SR * FRAME_S)            # 30 ms frames, as the robot's stt_node
MAX_S, MIN_S = 12.0, 0.3
THREADS = 2
MIN_FREE_GB = 2.5                    # ~1.8 GB of models; the Pi froze once on a bigger load


def free_gb() -> float:
    for line in open('/proc/meminfo'):
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) / 1e6
    return 0.0


def load():
    if free_gb() < MIN_FREE_GB:
        sys.exit(f'only {free_gb():.1f} GB free, need {MIN_FREE_GB} GB -- close something and retry')
    models = []
    for name, make in (
        ('Parakeet 0.6B', lambda: LocalSherpaProvider(
            f'{MODELS}/sherpa/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8', THREADS)),
        ('Moonshine base', lambda: LocalSherpaProvider(
            f'{MODELS}/sherpa/sherpa-onnx-moonshine-base-en-int8', THREADS)),
        ('whisper tiny.en', lambda: LocalWhisperProvider(
            'tiny.en', f'{MODELS}/whisper', THREADS, beam_size=1)),
    ):
        t = time.time()
        m = make()
        m.transcribe(np.zeros(SR, np.float32), SR)        # warm
        print(f'  loaded {name} in {time.time() - t:.0f} s', flush=True)
        models.append((name, m))
    return models


def save_wav(path, pcm):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', type=float, default=0.6, help='silence that ends a sentence, s')
    ap.add_argument('--lead', type=float, default=0.3, help='audio kept before speech starts, s')
    ap.add_argument('--save', action='store_true', help='save each sentence as a WAV')
    a = ap.parse_args()
    end_frames, lead_frames = round(a.end / FRAME_S), round(a.lead / FRAME_S)
    if a.save:
        os.makedirs(SAVE_DIR, exist_ok=True)

    print('Loading models...', flush=True)
    models = load()
    vad = SileroVad(f'{MODELS}/vad/silero_vad.onnx')
    frames: queue.Queue = queue.Queue()
    work: queue.Queue = queue.Queue()

    def transcribe_loop():
        n = 0
        while True:
            pcm16 = work.get()
            n += 1
            pcm = pcm16.astype(np.float32) / 32768.0          # same samples, as floats
            peak = np.abs(pcm).max()
            print(f'#{n}  {len(pcm) / SR:.1f} s of speech, peak {peak:.2f}'
                  + ('  <-- CLIPPED' if peak > 0.98 else ''), flush=True)
            if a.save:
                path = f'{SAVE_DIR}/{n:02d}.wav'
                save_wav(path, pcm16)
                print(f'    saved {path}', flush=True)
            for name, m in models:
                t = time.time()
                text = m.transcribe(pcm, SR)
                print(f'    {name:<16} {int((time.time() - t) * 1000):>5} ms   {text or "(nothing)"}',
                      flush=True)
            print(flush=True)

    threading.Thread(target=transcribe_loop, daemon=True).start()
    lead = collections.deque(maxlen=lead_frames)
    utt, silence, active = [], 0, False
    print(f'\nSpeak English (sentence ends after {a.end} s of silence; Ctrl+C to quit)\n', flush=True)
    with sd.InputStream(samplerate=SR, channels=1, dtype='int16', blocksize=FRAME,
                        callback=lambda d, n, t, s: frames.put(d[:, 0].tobytes())):
        while True:
            f = frames.get()
            voiced = vad.is_speech(f, SR)
            if not active:
                lead.append(f)
                if voiced:
                    active, utt, silence = True, list(lead), 0
                continue
            utt.append(f)
            silence = 0 if voiced else silence + 1
            if silence < end_frames and len(utt) * FRAME_S < MAX_S:
                continue
            active = False
            lead.clear()
            if len(utt) * FRAME_S >= MIN_S:
                work.put(np.frombuffer(b''.join(utt), np.int16).copy())
            utt = []


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nbye')
