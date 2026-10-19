"""TTS provider interface — mirrors stt_providers/base.py exactly, same
reasoning: one file per provider + one entry in REGISTRY, and from_config()
owns each provider's env-var + param mapping so tts_node needs no
per-provider knowledge. Nothing else to touch when adding a fourth.
"""

from collections.abc import Mapping

import numpy as np


class ProviderUnavailable(Exception):
    """Raised on anything that should fall back to local_kokoro: missing API
    key, network error, non-2xx response, timeout, bad audio in the
    response."""


class TTSProvider:
    name = "base"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "TTSProvider":
        """Build this provider from the node's params dict + process environment
        (for API keys). Each provider owns its own env-var name and param
        mapping here — that keeps tts_node free of per-provider knowledge.
        `params` keys are documented where tts_node assembles them. Raise
        ProviderUnavailable if it can't be built; the node falls back to local."""
        raise NotImplementedError

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Returns (samples float32 mono [-1,1], sample_rate). Raises
        ProviderUnavailable if the provider itself failed (caller falls
        back to local_kokoro)."""
        raise NotImplementedError
