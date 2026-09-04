"""TTS provider registry — mirrors stt_providers/__init__.py."""

from .base import ProviderUnavailable, TTSProvider
from .local_kokoro import LocalKokoroProvider
from .sarvam import SarvamTTSProvider
from .soniox import SonioxTTSProvider

REGISTRY = {
    'local': LocalKokoroProvider,
    'sarvam': SarvamTTSProvider,
    'soniox': SonioxTTSProvider,
}

__all__ = ['ProviderUnavailable', 'TTSProvider', 'REGISTRY']
