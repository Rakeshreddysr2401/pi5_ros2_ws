"""STT provider interface — every provider (local or cloud) implements this.

Adding a new provider: one file here + one line in __init__.py's REGISTRY.
Mirrors the ProviderSpec pattern langrobo_core's MCP service uses for the
same reason — one place to look, not scattered if/elif chains.
"""

import numpy as np


class ProviderUnavailable(Exception):
    """Raised on anything that should fall back to local_whisper: missing
    API key, network error, non-2xx response, timeout. Never raised for
    "no speech detected" — that's a normal empty-string result, not a
    provider failure.
    """


class STTProvider:
    name = "base"

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        """pcm: float32 mono, range [-1, 1]. Returns text, '' if nothing
        recognized. Raises ProviderUnavailable if the provider itself
        failed (caller falls back to local_whisper)."""
        raise NotImplementedError
