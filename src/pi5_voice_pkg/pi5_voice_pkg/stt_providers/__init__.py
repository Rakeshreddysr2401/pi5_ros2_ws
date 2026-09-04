"""STT provider registry. Add a provider: one file next to this one + one
entry in REGISTRY. Nothing else needs to change to add a fourth one.
"""

from .base import ProviderUnavailable, STTProvider
from .local_whisper import LocalWhisperProvider
from .sarvam import SarvamProvider
from .soniox import SonioxProvider

REGISTRY = {
    'local': LocalWhisperProvider,
    'sarvam': SarvamProvider,
    'soniox': SonioxProvider,
}

__all__ = ['ProviderUnavailable', 'STTProvider', 'REGISTRY']
