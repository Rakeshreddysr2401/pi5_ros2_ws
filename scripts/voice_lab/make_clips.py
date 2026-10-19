"""Test clips: the PHRASES in 4 Kokoro voices (2 Indian-accent) + 30 s of real room noise.
    python3 make_clips.py OUT_DIR"""
import os, sys, numpy as np, onnxruntime as ort, sounddevice as sd
from kokoro_onnx import Kokoro
from scipy.signal import resample_poly
sys.path.insert(0, os.path.dirname(__file__)); from phrases import PHRASES
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
M = os.path.expanduser('~/ros2_ws/src/langrobo_ros/models/kokoro/')
noise = sd.rec(30 * 16000, samplerate=16000, channels=1, dtype='float32'); sd.wait()
np.save(f'{out}/room_noise.npy', noise[:, 0])
so = ort.SessionOptions(); so.intra_op_num_threads = 3
k = Kokoro.from_session(ort.InferenceSession(M + 'kokoro-v1.0.onnx', so, providers=['CPUExecutionProvider']), M + 'voices-v1.0.bin')
for v in ['af_heart', 'am_michael', 'hf_alpha', 'hm_omega']:
    for i, p in enumerate(PHRASES):
        s, sr = k.create(p, voice=v, speed=1.0, lang='en-us')
        np.save(f'{out}/{v}_{i:02d}.npy', resample_poly(s, 2, 3).astype(np.float32))  # 24k -> 16k
    print('done', v, flush=True)
