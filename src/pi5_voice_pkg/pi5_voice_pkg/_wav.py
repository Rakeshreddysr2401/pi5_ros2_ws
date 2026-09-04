"""WAV <-> float32 PCM, shared by every cloud STT and TTS provider (moved up
from stt_providers/ so tts_providers/ doesn't need its own copy). STT encodes
(mic audio -> upload); TTS decodes (API response -> playback samples).
"""

import io
import wave

import numpy as np


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int) -> bytes:
    pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def wav_bytes_to_pcm(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes), 'rb') as w:
        sample_rate = w.getframerate()
        raw = w.readframes(w.getnframes())
        width = w.getsampwidth()
        channels = w.getnchannels()
    if width != 2:
        raise ValueError(f'expected 16-bit WAV, got {width * 8}-bit')
    pcm16 = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        pcm16 = pcm16.reshape(-1, channels)[:, 0]  # first channel only
    return pcm16.astype(np.float32) / 32768.0, sample_rate
