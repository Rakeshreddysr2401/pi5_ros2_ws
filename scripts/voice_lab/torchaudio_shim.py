"""Bench-only stand-in: the image torchaudio is built for CUDA 13.2, torch for 13.0.
Indic-Transcribe uses torchaudio ONLY to resample non-16 kHz audio; ours is 16 kHz."""
class _NoResample:
    def __init__(self, *a, **k): raise RuntimeError("resampling needed but torchaudio is unavailable")
class transforms: Resample = _NoResample
class functional:
    @staticmethod
    def resample(*a, **k): raise RuntimeError("resampling needed but torchaudio is unavailable")
