"""sherpa-onnx offline recognisers on CPU — Moonshine and NeMo Parakeet.

Measured on the Pi 5 (2 threads, 2026-10-10), 96 clips of Mitra commands in
four voices mixed with real room noise from the USB AM-C28 array:

  whisper tiny.en (the old local)   WER 3.6 %   "Mitra" 24/24   1.2 s a clip
  Moonshine base int8               WER 3.6 %   "Mitra" 24/24   0.2 s a clip

Same accuracy, about six times sooner: Moonshine encodes only as much audio as
was spoken, where Whisper always pays for a 30 s window. docs/voice/PI5_VOICE.md
has the full table.

The model directory is one of the sherpa-onnx release tarballs
(models/README.md); its files say which kind it is. Anything that fails to
load raises ProviderUnavailable, and stt_node keeps Whisper.
"""

import os
from collections.abc import Mapping

import numpy as np

from .base import ProviderUnavailable, STTProvider


def _first(d: str, *names: str) -> str | None:
    for n in names:
        if os.path.exists(os.path.join(d, n)):
            return os.path.join(d, n)
    return None


class LocalSherpaProvider(STTProvider):
    name = "sherpa"

    @classmethod
    def from_config(cls, params: dict, env: Mapping[str, str]) -> "LocalSherpaProvider":
        model_dir = (params.get('sherpa_model_dir') or '').strip()
        if not model_dir:
            raise ProviderUnavailable('stt_sherpa_model_dir is not set')
        return cls(model_dir, int(params.get('threads', 2)))

    def __init__(self, model_dir: str, threads: int = 2):
        try:
            import sherpa_onnx
        except ImportError as e:
            raise ProviderUnavailable(f'sherpa-onnx not installed ({e})') from e
        d = os.path.expanduser(model_dir)
        tokens = _first(d, 'tokens.txt')
        if not tokens:
            raise ProviderUnavailable(f'no tokens.txt in {d!r}')
        try:
            if _first(d, 'preprocess.onnx'):                      # Moonshine
                self._rec = sherpa_onnx.OfflineRecognizer.from_moonshine(
                    preprocessor=_first(d, 'preprocess.onnx'),
                    encoder=_first(d, 'encode.int8.onnx', 'encode.onnx'),
                    uncached_decoder=_first(d, 'uncached_decode.int8.onnx', 'uncached_decode.onnx'),
                    cached_decoder=_first(d, 'cached_decode.int8.onnx', 'cached_decode.onnx'),
                    tokens=tokens, num_threads=threads)
                self.kind = 'moonshine'
            elif _first(d, 'joiner.int8.onnx', 'joiner.onnx'):     # Parakeet (NeMo transducer)
                self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=_first(d, 'encoder.int8.onnx', 'encoder.onnx'),
                    decoder=_first(d, 'decoder.int8.onnx', 'decoder.onnx'),
                    joiner=_first(d, 'joiner.int8.onnx', 'joiner.onnx'),
                    tokens=tokens, model_type='nemo_transducer', num_threads=threads)
                self.kind = 'transducer'
            else:
                raise ProviderUnavailable(f'{d!r} is not a Moonshine or Parakeet model')
        except ProviderUnavailable:
            raise
        except Exception as e:
            raise ProviderUnavailable(f'cannot load sherpa model {d!r} ({e})') from e

    def transcribe(self, pcm: np.ndarray, sample_rate: int) -> str:
        try:
            stream = self._rec.create_stream()
            stream.accept_waveform(sample_rate, np.ascontiguousarray(pcm, dtype=np.float32))
            self._rec.decode_stream(stream)
            return stream.result.text.strip()
        except Exception as e:
            raise ProviderUnavailable(f'sherpa decode failed ({e})') from e
