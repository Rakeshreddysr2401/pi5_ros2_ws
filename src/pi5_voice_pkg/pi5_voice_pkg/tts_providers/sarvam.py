"""Sarvam AI Bulbul v3 — text-to-speech. API spec:
docs.sarvam.ai/api-reference/text-to-speech/convert

₹30/10K input characters, sub-250ms streaming latency (this uses the
simpler non-streaming REST call — matches how tts_node already speaks one
full sentence per call). Response is base64-encoded WAV, decoded with the
shared _wav helper. See PI5_VOICE.md for the cost/latency comparison
against Soniox and against local Kokoro (measured RTF ~1.8 — this is the
"why": both cloud TTS options are meaningfully faster).

speech_sample_rate is pinned to 24000 explicitly — verified live 2026-09-04:
Sarvam's actual default is 22050 Hz (not 24000 as the docs summary implied),
and the Blackwire headset's ALSA output rejects 22050 ("Invalid sample
rate", PaErrorCode -9997) while 24000 is already the rate Kokoro plays at
successfully on this same hardware. Don't remove this without confirming
whatever rate you switch to is one this specific headset actually accepts.
"""

import base64

import numpy as np
import requests

from .base import ProviderUnavailable, TTSProvider
from .._wav import wav_bytes_to_pcm

ENDPOINT = 'https://api.sarvam.ai/text-to-speech'
TIMEOUT_S = 8.0


class SarvamTTSProvider(TTSProvider):
    name = "sarvam"

    def __init__(self, api_key: str, language: str = 'en-IN', speaker: str = 'ritu'):
        if not api_key:
            raise ProviderUnavailable('SARVAM_API_KEY not set')
        self._api_key = api_key
        self._language = language
        self._speaker = speaker

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        try:
            resp = requests.post(
                ENDPOINT,
                headers={'api-subscription-key': self._api_key, 'Content-Type': 'application/json'},
                json={
                    'text': text, 'model': 'bulbul:v3', 'language_code': self._language,
                    'speaker': self._speaker, 'output_audio_codec': 'wav',
                    'speech_sample_rate': 24000,
                },
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise ProviderUnavailable(f'sarvam request failed: {e}') from e
        if resp.status_code != 200:
            raise ProviderUnavailable(f'sarvam HTTP {resp.status_code}: {resp.text[:200]}')
        try:
            audios = resp.json()['audios']
            wav_bytes = base64.b64decode(audios[0])
            return wav_bytes_to_pcm(wav_bytes)
        except (ValueError, KeyError, IndexError) as e:
            raise ProviderUnavailable(f'sarvam bad response: {e}') from e
