"""Sarvam, streamed: English reply -> Telugu text (translate, kept-alive HTTP)
-> Telugu speech (bulbul:v3 over ONE persistent WebSocket, audio yielded as
it arrives).

Why (owner, 2026-10-04: "STT and TTS at good speed, without delay"). Measured
on the Pi 5 the same morning, per sentence:
    sarvam_translate (REST translate + REST bulbul)  ~1.8 s before any sound
    translate on a kept-alive session                ~0.6 s (fresh TLS: ~0.72)
    bulbul over the WebSocket: first audio 0.18 s, whole sentence ~0.7 s,
    the socket reused sentence after sentence (config once, 0.19 s)
so sound starts ~0.8 s after the brain's sentence instead of ~1.8 s.

stream(text) yields (float32 mono samples, 24000) chunks; tts_node plays each
as it comes. synthesize(text) joins them (the startup cues). Text already in
Telugu script (the cues) is not translated. Any failure before the first
chunk raises ProviderUnavailable -> tts_node falls back (tts_fallback: piper);
the socket is rebuilt on the next sentence.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
from collections.abc import Iterator, Mapping

import numpy as np
import requests

from .base import ProviderUnavailable, TTSProvider

WS_URL = "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true"
TRANSLATE_URL = "https://api.sarvam.ai/translate"
SAMPLE_RATE = 24000
FIRST_AUDIO_TIMEOUT_S = 6.0
CHUNK_TIMEOUT_S = 8.0
_TELUGU = re.compile(r"[ఀ-౿]")
_DONE = object()


class SarvamStreamProvider(TTSProvider):
    name = "sarvam_stream"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "SarvamStreamProvider":
        key = env.get("SARVAM_API_KEY", "")
        if not key:
            raise ProviderUnavailable("SARVAM_API_KEY not set")
        src = params.get("translate_from") or "en"
        dst = params.get("translate_to") or "te"
        return cls(api_key=key, source=f"{src}-IN", target=f"{dst}-IN",
                   speaker=params.get("sarvam_voice") or "ritu")

    def __init__(self, api_key: str, source: str = "en-IN", target: str = "te-IN",
                 speaker: str = "ritu"):
        self._key = api_key
        self._source, self._target, self._speaker = source, target, speaker
        from ..stt_providers.sarvam import warm_session
        self._http = warm_session(api_key)        # kept-alive + kept warm
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="sarvam_ws").start()
        self._ws = None
        self._lock = threading.Lock()      # one sentence on the socket at a time

    # ── translate ───────────────────────────────────────────────────────────

    def translate(self, text: str) -> str:
        if self._source == self._target or _TELUGU.search(text):
            return text
        try:
            r = self._http.post(TRANSLATE_URL, timeout=10, json={
                "input": text, "source_language_code": self._source,
                "target_language_code": self._target})
        except requests.RequestException as e:
            raise ProviderUnavailable(f"sarvam translate: {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise ProviderUnavailable(f"sarvam translate HTTP {r.status_code}: {r.text[:200]}")
        out = (r.json().get("translated_text") or "").strip()
        if not out:
            raise ProviderUnavailable("sarvam translate returned no text")
        return out

    # ── speech over the WebSocket ───────────────────────────────────────────

    async def _connect(self):
        import websockets
        ws = await websockets.connect(WS_URL, additional_headers={"Api-Subscription-Key": self._key},
                                      open_timeout=8, ping_interval=20, max_size=None)
        await ws.send(json.dumps({"type": "config", "data": {
            "language_code": self._target, "speaker": self._speaker, "model": "bulbul:v3",
            "output_audio_codec": "linear16", "speech_sample_rate": str(SAMPLE_RATE),
            "min_buffer_size": 30, "max_chunk_length": 150}}))
        return ws

    async def _speak(self, text: str, out: "asyncio.Queue"):
        try:
            for attempt in (1, 2):          # a socket closed while idle: rebuild once
                try:
                    if self._ws is None:
                        self._ws = await self._connect()
                    await self._ws.send(json.dumps({"type": "text", "data": {"text": text}}))
                    await self._ws.send(json.dumps({"type": "flush"}))
                    break
                except Exception:
                    self._ws = None
                    if attempt == 2:
                        raise
            got = False
            while True:
                msg = json.loads(await asyncio.wait_for(
                    self._ws.recv(), CHUNK_TIMEOUT_S if got else FIRST_AUDIO_TIMEOUT_S))
                kind = msg.get("type")
                if kind == "audio":
                    pcm = np.frombuffer(base64.b64decode(msg["data"]["audio"]), dtype=np.int16)
                    out.put_nowait(pcm.astype(np.float32) / 32768.0)
                    got = True
                elif kind == "event" and msg["data"].get("event_type") == "final":
                    break
                elif kind == "error":
                    raise ProviderUnavailable(f"sarvam tts {msg['data'].get('code')}: "
                                              f"{msg['data'].get('message')}")
            out.put_nowait(_DONE)
        except Exception as e:
            self._ws = None
            out.put_nowait(e if isinstance(e, ProviderUnavailable)
                           else ProviderUnavailable(f"sarvam tts stream: {type(e).__name__}: {e}"))

    def stream(self, text: str) -> Iterator[tuple[np.ndarray, int]]:
        """Telugu speech for `text`, chunk by chunk. Raises ProviderUnavailable
        if it fails before the first chunk; a failure later just ends it."""
        te = self.translate(text)
        with self._lock:
            q: asyncio.Queue = asyncio.Queue()
            fut = asyncio.run_coroutine_threadsafe(self._speak(te, q), self._loop)
            first = True
            while True:
                try:
                    item = asyncio.run_coroutine_threadsafe(
                        asyncio.wait_for(q.get(), CHUNK_TIMEOUT_S + 2), self._loop).result()
                except Exception as e:
                    fut.cancel()
                    if first:
                        raise ProviderUnavailable(f"sarvam tts stream timed out ({e})") from e
                    return
                if item is _DONE:
                    return
                if isinstance(item, Exception):
                    if first:
                        raise item
                    return
                first = False
                yield item, SAMPLE_RATE

    def reset(self) -> None:
        """Drop the socket (a sentence was abandoned mid-stream); the next one reconnects."""
        ws, self._ws = self._ws, None
        if ws is not None:
            asyncio.run_coroutine_threadsafe(ws.close(), self._loop)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        parts = [c for c, _ in self.stream(text)]
        if not parts:
            raise ProviderUnavailable("sarvam tts stream returned no audio")
        return np.concatenate(parts), SAMPLE_RATE
