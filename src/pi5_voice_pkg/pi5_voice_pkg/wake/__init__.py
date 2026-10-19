"""Wake-word detector registry. Add a detector: one file next to this one +
one entry in REGISTRY. Nothing else changes (see wake/base.py).

'transcript_alias' is not a detector here — it is the legacy no-acoustic-model
fallback that lives in stt_node (it gates on a name in the transcript, which
requires transcribing everything first). Selecting it means "no wake layer".
"""

from .base import WakeDetector, WakeUnavailable
from .openwakeword_detector import OpenWakeWordDetector

REGISTRY = {
    'openwakeword': OpenWakeWordDetector,
}

__all__ = ['WakeDetector', 'WakeUnavailable', 'REGISTRY']
