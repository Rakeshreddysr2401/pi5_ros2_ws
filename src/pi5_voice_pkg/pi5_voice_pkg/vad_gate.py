"""Should this captured segment be sent for transcription at all?

webrtcvad answers "is this frame speech-like", which is a low bar: a fan, a
door, a TV across the room all pass. A cloud recogniser handed such a segment
does not return empty — it returns a confident, fluent, entirely invented
sentence ("This is ₹11,800." was produced from an empty room, 2026-09-05), and
with the wake gate open every one of those becomes a turn the robot answers.

So there is a second gate here, between the VAD and the recogniser:

  * **duration** — a blip cannot be a command.
  * **energy** — real speech near the mic is louder than the room's noise floor.
  * **voiced ratio** — a genuine utterance is mostly voiced frames; a noise
    burst that tripped the VAD once is mostly silence.

Cheap (three floats), local (nothing leaves the Pi until it passes) and pure —
no rclpy, no audio device — so the thresholds can be tuned against recordings.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GateConfig:
    """Thresholds. Defaults are deliberately permissive: a dropped command is
    far worse than an occasional hallucination getting through."""
    min_frames: int = 10          # ~300ms at 30ms frames
    # Measured room noise floor sat near 0.029 on a Bluetooth mic (bt_profile:
    # hfp — the mic currently configured), speech peaked around 0.67. The
    # threshold must clear the noise floor with margin or this gate does not
    # do what it exists to do — 0.012 shipped below 0.029 (fixed 2026-09-05:
    # every idle-room reading passed "too quiet" and only voiced_ratio stood
    # between ambient Bluetooth-mic noise and another hallucinated transcript).
    min_rms: float = 0.05
    min_voiced_ratio: float = 0.35


@dataclass(frozen=True)
class GateResult:
    keep: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.keep


def evaluate(frames: int, rms: float, voiced_frames: int,
             cfg: GateConfig = GateConfig()) -> GateResult:
    """Decide whether a captured utterance is worth transcribing.

    `reason` names the failed test so /voice/debug_vad shows why something was
    dropped — a silent filter is impossible to tune.
    """
    if frames < cfg.min_frames:
        return GateResult(False, f"too short ({frames} frames < {cfg.min_frames})")
    if rms < cfg.min_rms:
        return GateResult(False, f"too quiet (rms {rms:.4f} < {cfg.min_rms})")
    ratio = voiced_frames / frames if frames else 0.0
    if ratio < cfg.min_voiced_ratio:
        return GateResult(False, f"mostly silence (voiced {ratio:.2f} < {cfg.min_voiced_ratio})")
    return GateResult(True, f"rms {rms:.4f}, voiced {ratio:.2f}")
