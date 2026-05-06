from langchain_core.tools import tool

from . import _bridge


@tool
def speak(text: str) -> str:
    """Say something to the user immediately via the robot's speaker.

    Call this at the start of any long task — the user hears a reply right
    away instead of silence while tools are running."""
    _bridge.get().publish_speech(text)
    return f"Spoken: {text}"
