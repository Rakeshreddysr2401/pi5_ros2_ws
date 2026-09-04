"""Sarvam AI Saaras v3 — speech-to-text-translate (any of 22 Indic languages
-> English text, in one call). API spec: docs.sarvam.ai/api-reference/speech-to-text/transcribe

₹30/10K transcript characters, <150ms time-to-first-token in fast streaming
mode (this uses the simpler REST endpoint, not streaming — matches how
stt_node already batches one full utterance per call). See PI5_VOICE.md for
the cost/latency comparison against Soniox that led to adding this.
"""

from collections.abc import Mapping

import numpy as np
import requests

from .base import ProviderUnavailable, STTProvider
from .._wav import pcm_to_wav_bytes

ENDPOINT = 'https://api.sarvam.ai/speech-to-text'
TIMEOUT_S = 8.0


class SarvamProvider(STTProvider):
    name = "sarvam"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "SarvamProvider":
        # Sarvam wants BCP-47 with region (te-IN); the node param is just 'te'.
        return cls(api_key=env.get('SARVAM_API_KEY', ''),
                   source_language=f"{params['source_language']}-IN")

    def __init__(self, api_key: str, source_language: str = 'te-IN', model: str = 'saaras:v3'):
        if not api_key:
            raise ProviderUnavailable('SARVAM_API_KEY not set')
        self._api_key = api_key
        self._language = source_language
        self._model = model

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        wav = pcm_to_wav_bytes(pcm, sample_rate)
        try:
            resp = requests.post(
                ENDPOINT,
                headers={'api-subscription-key': self._api_key},
                files={'file': ('utterance.wav', wav, 'audio/wav')},
                data={'model': self._model, 'mode': 'translate', 'language_code': self._language},
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise ProviderUnavailable(f'sarvam request failed: {e}') from e
        if resp.status_code != 200:
            raise ProviderUnavailable(f'sarvam HTTP {resp.status_code}: {resp.text[:200]}')
        try:
            return resp.json().get('transcript', '').strip()
        except ValueError as e:
            raise ProviderUnavailable(f'sarvam bad response: {e}') from e
