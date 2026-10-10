"""High-pass filter for the mic, applied frame by frame before anything else.

Why (measured 2026-10-10 on the USB AM-C28 conference array): with nobody
talking, 98% of what the mic delivered was below 100 Hz -- mostly 2-7 Hz,
swinging rms 0.02-0.52 every 100 ms (air or vibration on the mic, not sound).
Speech has almost nothing below ~85 Hz. That rumble alone made webrtcvad call
63% of an empty room "speech" and kept utterances running to the cap. A 100 Hz
high-pass took the room from rms 0.093 to 0.018 and leaves the voice alone.

The array does its own echo cancelling and voice pickup; this removes only the
part it lets through that no recogniser needs. Pure: no rclpy, no audio device.
"""

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi


class HighPass:
    """Streaming Butterworth high-pass on int16 frames; keeps state across frames."""

    def __init__(self, cutoff_hz: float, sample_rate: int = 16000, order: int = 4):
        self._sos = butter(order, cutoff_hz, 'highpass', fs=sample_rate, output='sos')
        self._zi = sosfilt_zi(self._sos) * 0.0

    def __call__(self, frame: bytes) -> bytes:
        x = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
        y, self._zi = sosfilt(self._sos, x, zi=self._zi)
        return np.clip(np.round(y), -32768, 32767).astype(np.int16).tobytes()
