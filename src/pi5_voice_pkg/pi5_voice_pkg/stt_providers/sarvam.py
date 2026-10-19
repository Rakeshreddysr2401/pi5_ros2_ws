"""Sarvam AI Saaras v3 — speech-to-text-translate (any of 22 Indic languages
-> English text, in one call). API spec: docs.sarvam.ai/api-reference/speech-to-text/transcribe

₹30/10K transcript characters, <150ms time-to-first-token in fast streaming
mode (this uses the simpler REST endpoint, not streaming — matches how
stt_node already batches one full utterance per call). See docs/voice/PI5_VOICE.md for
the cost/latency comparison against Soniox that led to adding this.
"""

from collections.abc import Mapping

import numpy as np
import requests

from .base import ProviderUnavailable, STTProvider
from .._wav import pcm_to_wav_bytes

ENDPOINT = 'https://api.sarvam.ai/speech-to-text'
TIMEOUT_S = 8.0



# One kept-alive HTTPS connection per provider, kept warm: a fresh TLS
# handshake to api.sarvam.ai cost ~0.11 s per call on the Pi 5 (translate:
# 0.72 s -> 0.61 s, 2026-10-04), and the server drops idle connections, so the
# first sentence after a quiet minute paid it again. A HEAD every 45 s is free
# (no model runs) and keeps the socket open.
KEEP_WARM_S = 45.0


def warm_session(api_key: str):
    """A requests.Session with the key set and a daemon thread keeping it warm."""
    import threading
    import time
    s = requests.Session()
    s.headers['api-subscription-key'] = api_key

    def keep():
        while True:
            time.sleep(KEEP_WARM_S)
            try:
                s.head('https://api.sarvam.ai/', timeout=5)
            except requests.RequestException:
                pass

    threading.Thread(target=keep, daemon=True, name='sarvam_keepwarm').start()
    return s

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
        self._http = warm_session(api_key)

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        wav = pcm_to_wav_bytes(pcm, sample_rate)
        try:
            resp = self._http.post(
                ENDPOINT,
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
