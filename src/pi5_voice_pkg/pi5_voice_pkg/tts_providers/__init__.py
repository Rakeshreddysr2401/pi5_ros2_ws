"""TTS provider registry — mirrors stt_providers/__init__.py."""

from .base import ProviderUnavailable, TTSProvider
from .local_kokoro import LocalKokoroProvider
from .local_piper import LocalPiperProvider
from .sarvam import SarvamTTSProvider
from .sarvam_translate import SarvamTranslateTTSProvider
from .sarvam_stream import SarvamStreamProvider
from .soniox import SonioxTTSProvider

REGISTRY = {
    'local': LocalKokoroProvider,
    'piper': LocalPiperProvider,      # fast local (RTF ~0.25 vs Kokoro ~2)
    'sarvam': SarvamTTSProvider,
    'sarvam_translate': SarvamTranslateTTSProvider,  # English text -> Telugu speech
    'sarvam_stream': SarvamStreamProvider,           # the same, streamed (first sound ~0.8 s)
    'soniox': SonioxTTSProvider,
}

__all__ = ['ProviderUnavailable', 'TTSProvider', 'REGISTRY']
