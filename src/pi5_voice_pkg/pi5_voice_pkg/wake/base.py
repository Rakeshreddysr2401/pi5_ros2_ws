"""Wake-word detector interface — mirrors stt_providers/base.py.

A detector consumes raw audio frames and fires when the wake word is heard,
BEFORE any transcription. That is the whole point: while asleep, stt_node
runs only this (cheap) detector and transcribes nothing — no ambient speech
is ever sent to a cloud STT provider (cost + privacy), unlike the
transcript-alias fallback which must transcribe everything to check for a
name in the text.

Adding a detector: one file here (subclass WakeDetector, implement
process()/reset()/from_config()) + one line in __init__.py's REGISTRY.
from_config() owns the detector's own model path / key / env var, so
stt_node carries no per-detector knowledge.
"""

from collections.abc import Mapping


class WakeUnavailable(Exception):
    """Raised when a detector can't be built (missing lib, missing model
    file, bad config). stt_node falls back to the transcript-alias gate —
    same 'degrade, never crash' rule as the STT providers (CLAUDE.md #4)."""


class WakeDetector:
    name = "base"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "WakeDetector":
        """Build from the node's params dict + environment. Raise
        WakeUnavailable if it can't be constructed."""
        raise NotImplementedError

    def process(self, frame: bytes) -> bool:
        """Feed one audio frame (16 kHz mono int16 PCM bytes, the same frame
        stt_node's VAD gets). Return True the instant the wake word is
        detected, else False. Must be cheap enough to run on every frame."""
        raise NotImplementedError

    def reset(self) -> None:
        """Clear any internal streaming buffer — called when going back to
        sleep and right after a detection, so stale audio can't re-trigger."""
