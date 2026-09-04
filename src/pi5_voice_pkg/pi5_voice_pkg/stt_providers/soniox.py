"""Soniox real-time STT with translation, used in one-shot batch mode (send
a whole utterance, read until `finished`) to match how stt_node already
segments audio — same WebSocket API, just not kept open across utterances.
API spec: soniox.com/docs/stt/rt/real-time-transcription

$0.12/hr real-time, 260ms median latency in true streaming use (not the
figure that applies here, since this sends one utterance and waits). See
PI5_VOICE.md for the cost/latency comparison against Sarvam.

UNVERIFIED — no Soniox API key available at time of writing. The request/
response shapes below are transcribed from the docs, not confirmed against
a real session. Sanity-check the token-joining logic (do translation tokens
already carry their own leading spaces, or does this need `' '.join`
instead of `''.join`?) against real output before trusting this in
production.
"""

import asyncio
import json

import numpy as np
import websockets

from .base import ProviderUnavailable, STTProvider
from .._wav import pcm_to_wav_bytes

WS_URL = 'wss://stt-rt.soniox.com/transcribe-websocket'
TIMEOUT_S = 8.0


class SonioxProvider(STTProvider):
    name = "soniox"

    def __init__(self, api_key: str, source_language: str = 'te', target_language: str = 'en'):
        if not api_key:
            raise ProviderUnavailable('SONIOX_API_KEY not set')
        self._api_key = api_key
        self._source = source_language
        self._target = target_language

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        wav = pcm_to_wav_bytes(pcm, sample_rate)
        try:
            return asyncio.run(asyncio.wait_for(self._run(wav), timeout=TIMEOUT_S))
        except (asyncio.TimeoutError, websockets.exceptions.WebSocketException, OSError) as e:
            raise ProviderUnavailable(f'soniox failed: {e}') from e

    async def _run(self, wav: bytes) -> str:
        async with websockets.connect(WS_URL) as ws:
            await ws.send(json.dumps({
                'api_key': self._api_key,
                'model': 'stt-rt-v5',
                'audio_format': 'auto',
                'language_hints': [self._source, self._target],
                'enable_endpoint_detection': True,
                'translation': {'type': 'one_way', 'target_language': self._target},
            }))
            await ws.send(wav)
            await ws.send('')

            parts = []
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get('error_code'):
                    raise ProviderUnavailable(f'soniox error {msg["error_code"]}: {msg.get("error_message")}')
                for tok in msg.get('tokens', []):
                    if tok.get('translation_status') == 'translation' and tok.get('is_final'):
                        parts.append(tok['text'])
                if msg.get('finished'):
                    break
            return ''.join(parts).strip()
