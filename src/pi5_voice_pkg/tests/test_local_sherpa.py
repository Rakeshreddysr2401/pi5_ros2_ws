import os
import pathlib

import numpy as np
import pytest

from pi5_voice_pkg.stt_providers import REGISTRY, ProviderUnavailable

MODELS = pathlib.Path(os.path.expanduser('~/ros2_ws/src/langrobo_ros/models/sherpa'))
MOONSHINE = MODELS / 'sherpa-onnx-moonshine-base-en-int8'
sherpa = REGISTRY['sherpa']


def test_unset_model_dir_degrades():
    with pytest.raises(ProviderUnavailable):
        sherpa.from_config({'sherpa_model_dir': ''}, {})


def test_wrong_dir_degrades(tmp_path):
    pytest.importorskip('sherpa_onnx')
    (tmp_path / 'tokens.txt').write_text('a 0\n')
    with pytest.raises(ProviderUnavailable):
        sherpa.from_config({'sherpa_model_dir': str(tmp_path)}, {})


@pytest.mark.skipif(not MOONSHINE.exists(), reason='moonshine not downloaded (models/README.md)')
def test_moonshine_loads_and_returns_text_for_silence():
    pytest.importorskip('sherpa_onnx')
    p = sherpa.from_config({'sherpa_model_dir': str(MOONSHINE), 'threads': 2}, {})
    assert p.kind == 'moonshine'
    assert isinstance(p.transcribe(np.zeros(16000, np.float32), 16000), str)
