import numpy as np

from pi5_voice_pkg.mic_filter import HighPass

SR = 16000


def _run(signal: np.ndarray) -> np.ndarray:
    hp = HighPass(100, SR)
    pcm = (signal * 12000).astype(np.int16)
    out = b''.join(hp(pcm[i:i + 480].tobytes()) for i in range(0, len(pcm), 480))
    return np.frombuffer(out, np.int16).astype(np.float64)


def _rms(x):
    return float(np.sqrt(np.mean(x[SR // 2:] ** 2)))     # skip the filter's settling


def test_rumble_is_removed():
    t = np.arange(2 * SR) / SR
    rumble = np.sin(2 * np.pi * 5 * t)                       # the AM-C28's 2-7 Hz
    assert _rms(_run(rumble)) < 0.01 * _rms(rumble * 12000)


def test_voice_band_passes_and_state_carries_across_frames():
    t = np.arange(2 * SR) / SR
    voice = np.sin(2 * np.pi * 300 * t)
    assert _rms(_run(voice)) > 0.95 * _rms(voice * 12000)   # no clicks at 30 ms frame edges
