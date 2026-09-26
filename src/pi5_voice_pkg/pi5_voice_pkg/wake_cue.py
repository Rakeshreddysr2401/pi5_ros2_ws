"""Should the robot answer "Yes boss" after hearing its name?

People call it two ways, and only one of them wants an answer:

    "Mitra."  ...pause...  "what's the time?"     <- the cue belongs here
    "Mitra, go to the kitchen"                    <- one breath, no pause

Speaking into the second one talks over the command, and the robot then
hears half a sentence. So the cue waits a moment after the wake word and
fires only if nobody has started talking by then.

Pure — no rclpy, no audio. stt_node feeds it the VAD's opinion frame by
frame; tts_node plays a clip it rendered once at startup.
"""

# Long enough that a continued sentence has begun (speech onset is well
# inside 300 ms), short enough that a real pause still feels answered.
DEFAULT_DELAY_S = 0.4


class CueGate:
    """Arms on the wake word, fires once if the pause holds."""

    def __init__(self, delay_s: float = DEFAULT_DELAY_S, enabled: bool = True):
        self._delay_s = max(0.0, float(delay_s))
        self._enabled = bool(enabled)
        self._deadline: float | None = None

    @property
    def armed(self) -> bool:
        return self._deadline is not None

    def on_wake(self, now: float) -> None:
        """The wake word just fired."""
        self._deadline = (now + self._delay_s) if self._enabled else None

    def update(self, voiced: bool, now: float) -> bool:
        """One audio frame. True exactly once, when the cue should be spoken."""
        if self._deadline is None:
            return False
        if voiced:
            self._deadline = None      # they kept talking — stay out of the way
            return False
        if now >= self._deadline:
            self._deadline = None
            return True
        return False

    def cancel(self) -> None:
        """Drop a pending cue (the utterance ended, or we went back to sleep)."""
        self._deadline = None
