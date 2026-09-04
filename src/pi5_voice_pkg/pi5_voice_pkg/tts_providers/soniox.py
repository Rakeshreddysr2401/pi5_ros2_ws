"""Soniox TTS v2 — text-to-speech. API spec:
soniox.com/docs/tts/rest-api/generate-speech

Simpler than the STT side: plain REST (not WebSocket), response is raw WAV
bytes directly (not base64, unlike Sarvam) — no session handshake needed.
~$0.70/hr of generated audio. See PI5_VOICE.md.

UNVERIFIED — no Soniox API key available at time of writing (same caveat as
stt_providers/soniox.py). Request shape is transcribed from docs, not
confirmed against a real response.

sample_rate is pinned to 24000 explicitly, same reason as sarvam.py: found
live that Sarvam's unstated default (22050) isn't a rate the Blackwire
headset's ALSA output accepts, while 24000 (what Kokoro already uses
successfully) is. Soniox's docs list 24000 as a valid explicit choice —
being explicit here avoids relying on whatever Soniox's own default is.
"""

import numpy as np
import requests

from .base import ProviderUnavailable, TTSProvider
from .._wav import wav_bytes_to_pcm

ENDPOINT = 'https://tts-rt.soniox.com/tts'
TIMEOUT_S = 8.0


class SonioxTTSProvider(TTSProvider):
    name = "soniox"

    def __init__(self, api_key: str, language: str = 'en', voice: str = 'Adrian'):
        if not api_key:
            raise ProviderUnavailable('SONIOX_API_KEY not set')
        self._api_key = api_key
        self._language = language
        self._voice = voice

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        try:
            resp = requests.post(
                ENDPOINT,
                headers={'Authorization': f'Bearer {self._api_key}'},
                json={
                    'text': text, 'model': 'tts-rt-v2', 'language': self._language,
                    'voice': self._voice, 'audio_format': 'wav', 'sample_rate': 24000,
                },
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise ProviderUnavailable(f'soniox request failed: {e}') from e
        if resp.status_code != 200:
            raise ProviderUnavailable(f'soniox HTTP {resp.status_code}: {resp.text[:200]}')
        try:
            return wav_bytes_to_pcm(resp.content)
        except (ValueError, EOFError) as e:
            raise ProviderUnavailable(f'soniox bad audio: {e}') from e
