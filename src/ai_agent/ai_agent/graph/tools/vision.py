from langchain_core.tools import tool

from . import _bridge


@tool
def query_vision(question: str) -> str:
    """Ask the robot's Moondream VLM a natural-language question about what
    the camera currently sees.

    Use for specific, targeted questions:
      'What colour is the bottle?'
      'Is there a person in the room?'
      'How far away is the chair?'
      'Do you see a dog? If yes, where — left, centre, or right?'

    The camera frame is already attached to LLM messages — use this only
    when you need a focused on-device VLM answer from Jetson's Moondream."""
    return _bridge.get().query_vision(question)
