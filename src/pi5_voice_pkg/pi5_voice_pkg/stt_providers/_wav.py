"""float32 PCM -> in-memory WAV bytes. Shared by every cloud provider — both
Sarvam (multipart file upload) and Soniox (audio_format='auto' over the
websocket) accept plain WAV, so there's one encoder instead of one per
provider.
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
