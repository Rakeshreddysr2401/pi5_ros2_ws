"""STT provider interface — every provider (local or cloud) implements this.

Adding a new provider is exactly two steps, nothing else:
  1. Add one file here with a class that subclasses STTProvider and
     implements transcribe() + from_config().
  2. Add one line to __init__.py's REGISTRY.
stt_node never learns the provider's name, constructor, or API-key env var —
from_config() owns all of that, so there are no per-provider if/elif chains
in the node. Mirrors the ProviderSpec pattern langrobo_core's MCP service
uses for the same reason — one place to look.
"""

from collections.abc import Mapping

import numpy as np


class ProviderUnavailable(Exception):
    """Raised on anything that should fall back to local_whisper: missing
    API key, network error, non-2xx response, timeout. Never raised for
    "no speech detected" — that's a normal empty-string result, not a
    provider failure.
    """


class STTProvider:
    name = "base"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "STTProvider":
        """Build this provider from the node's params dict + process environment
        (for API keys). Each provider owns its own env-var name and param
        mapping here — that is what keeps stt_node free of per-provider
        knowledge. `params` keys are documented where stt_node assembles them.
        Raise ProviderUnavailable if the provider can't be built (e.g. missing
        key); the node then falls back to local."""
        raise NotImplementedError

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        """pcm: float32 mono, range [-1, 1]. Returns text, '' if nothing
        recognized. Raises ProviderUnavailable if the provider itself
        failed (caller falls back to local_whisper)."""
        raise NotImplementedError
