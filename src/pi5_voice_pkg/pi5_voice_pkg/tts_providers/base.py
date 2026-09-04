"""TTS provider interface — mirrors stt_providers/base.py exactly, same
reasoning: one file per provider, one entry in REGISTRY, nothing else to
touch when adding a fourth.
"""

import numpy as np


class ProviderUnavailable(Exception):
    """Raised on anything that should fall back to local_kokoro: missing API
    key, network error, non-2xx response, timeout, bad audio in the
    response."""


class TTSProvider:
    name = "base"

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Returns (samples float32 mono [-1,1], sample_rate). Raises
        ProviderUnavailable if the provider itself failed (caller falls
        back to local_kokoro)."""
        raise NotImplementedError
