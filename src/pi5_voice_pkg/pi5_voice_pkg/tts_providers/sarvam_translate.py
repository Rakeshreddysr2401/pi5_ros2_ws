"""Sarvam translating TTS — English (or any source) text in, Telugu (or any
target) *speech* out. Two Sarvam calls per sentence:

  1. Text Translation (Mayura / sarvam-translate) source -> target text.
  2. Bulbul v3 TTS of the translated text, in the target language voice.

Why this exists: plain TTS speaks the text as given — hand English text to a
Telugu voice and you get mispronounced English, not Telugu. When the brain
replies in English but the household speaks Telugu, this provider does the
translation at the speech boundary so the robot is *heard* in Telugu.

Reuses SarvamTTSProvider for the synthesis half (same key, endpoint, the
24000 Hz headset-safe sample rate) — this class only adds the translate step.
Any failure (missing key, network, non-2xx, bad response) raises
ProviderUnavailable, so tts_node falls back to local Kokoro (English) rather
than going silent. API spec: docs.sarvam.ai/api-reference/text/translate
"""

from collections.abc import Mapping

import numpy as np
import requests

from .base import ProviderUnavailable, TTSProvider
from .sarvam import SarvamTTSProvider

TRANSLATE_ENDPOINT = 'https://api.sarvam.ai/translate'
TRANSLATE_MODEL = 'sarvam-translate:v1'
TIMEOUT_S = 8.0


class SarvamTranslateTTSProvider(TTSProvider):
    name = "sarvam_translate"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "SarvamTranslateTTSProvider":
        # translate_from/translate_to are bare codes ('en'/'te'); Sarvam wants
        # region-tagged BCP-47 (en-IN/te-IN) for both translate and TTS.
        return cls(
            api_key=env.get('SARVAM_API_KEY', ''),
            source_language=f"{params['translate_from']}-IN",
            target_language=f"{params['translate_to']}-IN",
            speaker=params['sarvam_voice'],
        )

    def __init__(self, api_key: str, source_language: str = 'en-IN',
                 target_language: str = 'te-IN', speaker: str = 'ritu'):
        if not api_key:
            raise ProviderUnavailable('SARVAM_API_KEY not set')
        self._api_key = api_key
        self._source = source_language
        self._target = target_language
        # The synthesis half: speak the translated text in the target language.
        self._tts = SarvamTTSProvider(api_key=api_key, language=target_language, speaker=speaker)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        translated = self._translate(text)
        return self._tts.synthesize(translated)

    def _translate(self, text: str) -> str:
        try:
            resp = requests.post(
                TRANSLATE_ENDPOINT,
                headers={'api-subscription-key': self._api_key, 'Content-Type': 'application/json'},
                json={
                    'input': text, 'model': TRANSLATE_MODEL,
                    'source_language_code': self._source,
                    'target_language_code': self._target,
                },
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise ProviderUnavailable(f'sarvam translate request failed: {e}') from e
        if resp.status_code != 200:
            raise ProviderUnavailable(f'sarvam translate HTTP {resp.status_code}: {resp.text[:200]}')
        try:
            return resp.json()['translated_text']
        except (ValueError, KeyError) as e:
            raise ProviderUnavailable(f'sarvam translate bad response: {e}') from e
