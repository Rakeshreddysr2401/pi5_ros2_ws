import json

from langchain_core.tools import tool

from . import _bridge


@tool
def query_vision(question: str) -> str:
    """Ask the robot's Moondream VLM a natural-language question about what
    the camera currently sees.

    Best for specific, targeted questions:
      'What colour is the bottle?'
      'Is there a person in the room?'
      'How far is the chair?'

    For broad scene descriptions the LLM already receives the camera frame —
    use this only when you need a focused on-device VLM answer."""
    return _bridge.get().query_vision(question)


@tool
def get_detected_objects() -> str:
    """Return a live list of objects detected by the Jetson's YOLO pipeline,
    with 3D positions, distances in metres, and direction (left/center/right).

    Use for fast spatial awareness without waiting for a full VLM response.
    Ideal for questions like 'what's nearby?' or 'where is the chair?'"""
    raw = _bridge.get().get_objects_json()
    try:
        objects = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw or "No objects detected."

    if not objects:
        return "No objects currently detected."

    lines = []
    for obj in objects:
        cls       = obj.get("class", "unknown")
        dist      = obj.get("distance_m")
        direction = obj.get("direction", "?")
        conf      = obj.get("confidence", 0.0)
        dist_str  = f"{dist:.1f} m" if dist is not None else "distance unknown"
        lines.append(f"- {cls} ({conf:.0%}): {dist_str}, {direction}")
    return "\n".join(lines)
