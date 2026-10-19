"""STT provider registry. Add a provider: one file next to this one + one
entry in REGISTRY. Nothing else needs to change to add a fourth one.
"""

from .apple import AppleProvider
from .base import ProviderUnavailable, STTProvider
from .local_sherpa import LocalSherpaProvider
from .local_whisper import LocalWhisperProvider
from .sarvam import SarvamProvider
from .soniox import SonioxProvider

REGISTRY = {
    'local': LocalWhisperProvider,
    'sherpa': LocalSherpaProvider,    # Moonshine / Parakeet, on the Pi (local_sherpa.py)
    'sarvam': SarvamProvider,
    'soniox': SonioxProvider,
    'apple': AppleProvider,           # Mac Mini's on-device Apple recogniser (apple.py)
}

__all__ = ['ProviderUnavailable', 'STTProvider', 'REGISTRY']
