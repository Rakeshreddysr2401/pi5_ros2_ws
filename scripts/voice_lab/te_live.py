#!/usr/bin/env python3
"""Live Telugu test: speak into the AM-C28, see Telugu text + English.

Listens on the default mic (16 kHz), cuts a sentence at ~0.6 s without a voice
(Silero VAD: webrtcvad called half of a noisy room "speech", so sentences never
ended and ran to the cap -- that was the 15 s, not Sarvam's 0.3-0.4 s),
sends it to the Jetson lab server (te_server.py, Indic-Transcribe + IndicTrans2)
and prints what came back. Ctrl+C to quit.

    python3 te_live.py            # local: Jetson GPU (Indic-Transcribe + IndicTrans2)
    python3 te_live.py --sarvam   # cloud: Sarvam saaras:v3 (SARVAM_API_KEY from ~/ros2_ws/.env)
    python3 te_live.py --sarvam --speak   # ...and SAY the English out loud (Sarvam bulbul:v3,
                                          # en-IN, through the default speaker = the Stone)

"రేపు మనం మూవీకి వెళ్దాం" -> prints the Telugu and "Let's go to a movie tomorrow",
then speaks that English. The mic is ignored while it speaks (and for the
Bluetooth delay after), so it never hears its own voice as a new sentence.
"""
import collections
import io
import json
import queue
import sys
import time
import urllib.request
import wave

import numpy as np
import onnxruntime as ort
import sounddevice as sd

URL = 'http://rakhi-jetson.local:8095/te'
SR, FRAME = 16000, 512              # Silero's window: 32 ms
END_SILENCE = 19                    # ~0.6 s without a voice ends a sentence
PRE_PAD = 10                        # ~0.3 s kept before speech starts
MAX_FRAMES = int(10 / 0.032)        # 10 s cap
MIN_RMS = 0.01
START, END = 0.5, 0.35              # voice probability: starts above, ends below
VAD_MODEL = '/home/rakhi24/ros2_ws/src/langrobo_ros/models/vad/silero_vad.onnx'


class Silero:
    """Silero VAD v5: probability that a 512-sample window holds a voice."""
    def __init__(self, path):
        o = ort.SessionOptions(); o.intra_op_num_threads = 1; o.inter_op_num_threads = 1
        self.s = ort.InferenceSession(path, o, providers=['CPUExecutionProvider'])
        self.state = np.zeros((2, 1, 128), np.float32); self.ctx = np.zeros(64, np.float32)
        self.sr = np.array(SR, dtype=np.int64)

    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32); self.ctx = np.zeros(64, np.float32)

    def prob(self, f):
        out, self.state = self.s.run(None, {'input': np.concatenate([self.ctx, f])[None, :],
                                            'state': self.state, 'sr': self.sr})
        self.ctx = f[-64:]
        return float(out[0, 0])


vad = Silero(VAD_MODEL)
frames: queue.Queue = queue.Queue()


def on_audio(indata, n, t, status):
    frames.put(indata[:, 0].copy())


def send(pcm: np.ndarray) -> dict:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
    req = urllib.request.Request(URL, data=buf.getvalue(), headers={'Content-Type': 'audio/wav'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


SARVAM_URL = 'https://api.sarvam.ai/speech-to-text'
_sarvam = None


def send_sarvam(pcm: np.ndarray) -> dict:
    """Two calls at once: Telugu text (transcribe) and English (translate)."""
    global _sarvam
    import os
    from concurrent.futures import ThreadPoolExecutor
    import requests
    from dotenv import load_dotenv
    if _sarvam is None:
        load_dotenv(os.path.expanduser('~/ros2_ws/.env'))
        _sarvam = requests.Session()
        _sarvam.headers['api-subscription-key'] = os.environ['SARVAM_API_KEY']
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
    wav = buf.getvalue()

    def call(mode):
        t = time.time()
        r = _sarvam.post(SARVAM_URL, files={'file': ('u.wav', wav, 'audio/wav')},
                         data={'model': 'saaras:v3', 'mode': mode, 'language_code': 'te-IN'}, timeout=15)
        r.raise_for_status()
        return r.json().get('transcript', ''), int((time.time() - t) * 1000)

    with ThreadPoolExecutor(2) as ex:
        te, en = ex.submit(call, 'transcribe'), ex.submit(call, 'translate')
        (te_text, te_ms), (en_text, en_ms) = te.result(), en.result()
    return {'te': te_text, 'en': en_text, 'asr_ms': te_ms, 'mt_ms': en_ms,
            'audio_s': round(len(pcm) / SR, 1), 'sarvam': True}


BT_DELAY_S = 0.8                    # Bluetooth plays this much behind the write
VOICE = 'ritu'                      # bulbul:v3 speaker, as the robot uses
_tts = None


def speak_english(text: str) -> int:
    """Say `text` in English through the default speaker; returns synthesis ms."""
    global _tts
    import os
    from dotenv import load_dotenv
    sys.path.insert(0, os.path.expanduser('~/ros2_ws/src/pi5_voice_pkg'))
    from pi5_voice_pkg.tts_providers.sarvam import SarvamTTSProvider
    if _tts is None:
        load_dotenv(os.path.expanduser('~/ros2_ws/.env'))
        _tts = SarvamTTSProvider(os.environ.get('SARVAM_API_KEY', ''), 'en-IN', VOICE)
    t = time.time()
    samples, sr = _tts.synthesize(text)
    ms = int((time.time() - t) * 1000)
    sd.play(samples, sr)
    sd.wait()
    time.sleep(BT_DELAY_S)
    return ms


def main():
    cloud = '--sarvam' in sys.argv
    speak = '--speak' in sys.argv
    print(f"Listening ({'Sarvam cloud' if cloud else 'Jetson local'}"
          f"{', speaking the English' if speak else ''})... speak Telugu (Ctrl+C to quit)\n", flush=True)
    ring = collections.deque(maxlen=PRE_PAD)
    speech, silence, active = [], 0, False
    with sd.InputStream(samplerate=SR, channels=1, dtype='float32', blocksize=FRAME, callback=on_audio):
        while True:
            f = frames.get()
            p = vad.prob(f)
            voiced = p >= (END if active else START)
            if not active:
                ring.append(f)
                if voiced:
                    active, speech, silence = True, list(ring), 0
                    print('  [hearing you...]', end='\r', flush=True)
                continue
            speech.append(f)
            silence = 0 if voiced else silence + 1
            if silence < END_SILENCE and len(speech) < MAX_FRAMES:
                continue
            pcm = np.concatenate(speech)
            active, speech = False, []
            ring.clear()
            rms = float(np.sqrt(np.mean(pcm ** 2)))
            if len(pcm) < SR * 0.4 or rms < MIN_RMS:
                print(' ' * 30, end='\r')
                continue
            t0 = time.time()
            try:
                r = send_sarvam(pcm) if cloud else send(pcm)
            except Exception as e:
                print(f"  !! {'Sarvam' if cloud else 'Jetson server'} failed: {e}")
                continue
            total = time.time() - t0
            print(f"Telugu : {r['te'] or '(nothing heard)'}")
            print(f"English: {r['en']}")
            if cloud:
                timing = f"Telugu call {r['asr_ms']} ms | English call {r['mt_ms']} ms (in parallel)"
            else:
                timing = f"speech->Telugu {r['asr_ms']} ms | Telugu->English {r['mt_ms']} ms"
            print(f"         {r['audio_s']} s of speech | {timing} | total {total:.1f} s | mic rms {rms:.3f}",
                  flush=True)
            if speak and r['en'].strip():
                try:
                    ms = speak_english(r['en'])
                    print(f"         spoke the English (voice ready in {ms} ms)", flush=True)
                except Exception as e:
                    print(f"  !! could not speak: {e}", flush=True)
                while not frames.empty():         # drop what the mic heard while it spoke
                    frames.get_nowait()
                vad.reset()
            print(flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nbye')
        sys.exit(0)
