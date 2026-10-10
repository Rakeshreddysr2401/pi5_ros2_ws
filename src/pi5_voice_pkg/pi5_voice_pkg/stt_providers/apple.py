"""Apple's on-device speech recogniser (the Siri/Dictation engine), run on the
Mac Mini by scripts/mac_mini/stt_server (mitra-stt.app) and called over the LAN
-- the same one-utterance-in, text-out shape as Sarvam's REST call.

    POST http://singireddys-mac-mini.local:8091/stt?lang=en-IN&context=Mitra
    body = WAV  ->  {"transcript": "...", "secs": 0.07, "on_device": true}

English only: Apple has no Telugu recogniser (no te-IN locale at all, probed
2026-10-10 on macOS 15.5), so this transcribes, it never translates. Audio
stays on the Mac (on-device only unless MAC_STT_SERVER=1).

Measured on the Mac (M4, 2026-10-10): 0.02-0.25 s a sentence; robot commands
synthesised and squeezed to 8 kHz (the Bluetooth HFP mic) came back word-for-
word, "Mitra" included thanks to the context hint. Not yet scored on the
owner's own recordings -- scripts/voice_lab/score_set.py runs it as 'apple'.

Settings (environment, like the cloud keys; all optional):
  MAC_STT_URL     default http://singireddys-mac-mini.local:8091/stt
  MAC_STT_LANG    default en-IN (en-US also has an on-device model)
  MAC_STT_SERVER  1 = let Apple's servers help when there is no on-device model
docs/voice/MAC_STT.md is the integration guide.
"""

from collections.abc import Mapping

import numpy as np
import requests

from .base import ProviderUnavailable, STTProvider
from .._wav import pcm_to_wav_bytes

DEFAULT_URL = 'http://singireddys-mac-mini.local:8091/stt'
TIMEOUT_S = 5.0


class AppleProvider(STTProvider):
    name = "apple"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "AppleProvider":
        # stt_hotwords ("Mitra") doubles as Apple's contextualStrings: without it
        # the name comes back as "Metro"/"Mira" more often.
        words = [w.strip() for w in (params.get('hotwords') or '').split(',') if w.strip()]
        context = words + [f'Hey {w}' for w in words]
        return cls(url=env.get('MAC_STT_URL') or DEFAULT_URL,
                   locale=env.get('MAC_STT_LANG') or 'en-IN',
                   context=context, allow_server=env.get('MAC_STT_SERVER') == '1')

    def __init__(self, url: str = DEFAULT_URL, locale: str = 'en-IN',
                 context: list[str] | None = None, allow_server: bool = False):
        self._url = url
        self._params = {'lang': locale, 'context': ','.join(context or [])}
        if allow_server:
            self._params['server'] = '1'
        self._http = requests.Session()
        # Not fatal at startup: the Mac may be asleep now and up later. Each
        # call that fails falls back to the local recogniser on its own.
        try:
            health = self._http.get(url.rsplit('/', 1)[0] + '/health', timeout=2).json()
            if health.get('auth') != 'authorized':
                raise ProviderUnavailable(
                    f"mitra-stt has no Speech Recognition permission ({health.get('auth')}); "
                    'open mitra-stt.app on the Mac and click Allow')
        except (requests.RequestException, ValueError):
            pass

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        wav = pcm_to_wav_bytes(pcm, sample_rate)
        try:
            resp = self._http.post(self._url, params=self._params, data=wav,
                                   headers={'Content-Type': 'audio/wav'}, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise ProviderUnavailable(f'mac stt request failed: {e}') from e
        if resp.status_code != 200:
            raise ProviderUnavailable(f'mac stt HTTP {resp.status_code}: {resp.text[:200]}')
        try:
            return resp.json().get('transcript', '').strip()
        except ValueError as e:
            raise ProviderUnavailable(f'mac stt bad response: {e}') from e
