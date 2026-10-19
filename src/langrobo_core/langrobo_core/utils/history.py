"""Cache-aware conversation-history trimming (pure functions — unit-testable
without ROS).

Trimming is the ONE deliberate KV-cache reset in the system: cutting messages
off the front shifts every remaining token, so the next request re-prefills
the whole kept suffix. Everything here exists to make that reset as rare and
as cheap as possible:

- Append-only within the cap: under `max_len` the list is returned untouched.
- Cuts land only on a real user HumanMessage — never a look()-injected camera
  frame (a conversation must not open with a dangling camera-view message)
  and never between an AIMessage's tool_call and its ToolMessage.
- Frame eviction piggybacks on the trim: since the suffix re-prefills anyway,
  that is the free moment to drop old camera frames (the most expensive tokens
  to re-prefill through the vision encoder). Between trims frames are kept —
  removing one mid-session would invalidate local_agent's cached prefix.
"""

from langchain_core.messages import HumanMessage

from .pose_stamp import CAMERA_VIEW_MARKER  # noqa: F401  (re-exported)

# Camera frames kept (newest first) when a trim fires. Two covers "compare with
# what you saw before" follow-ups; older scenes are stale by the time a trim
# happens and local_agent can always look() again.
KEEP_FRAMES_ON_TRIM = 2


def has_image(msg) -> bool:
    """True if msg still carries an image_url content block."""
    content = getattr(msg, "content", None)
    return isinstance(content, list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in content
    )


def is_camera_frame(msg) -> bool:
    """True for a look()-injected camera-view HumanMessage (live or evicted).

    Decided by look()'s label, not by carrying an image: a Telegram photo
    turn carries one too, and it is a real user message -- a turn boundary
    for trimming, and the request last_user_query must return."""
    if not isinstance(msg, HumanMessage):
        return False
    content = msg.content
    if isinstance(content, str):
        return content.startswith(CAMERA_VIEW_MARKER)
    if isinstance(content, list):
        return any(
            isinstance(p, dict) and p.get("text", "").startswith(CAMERA_VIEW_MARKER)
            for p in content
        )
    return False


def _evict_frame(msg):
    """Replace a frame message's image payload with a small text tombstone."""
    texts = [
        p.get("text", "")
        for p in msg.content
        if isinstance(p, dict) and p.get("type") == "text"
    ]
    label = " ".join(t for t in texts if t) or CAMERA_VIEW_MARKER
    return msg.model_copy(update={
        "content": f"{label} (frame removed to save space — call look() for a fresh view)"
    })


def trim_history(messages: list, max_len: int, keep_frames: int = KEEP_FRAMES_ON_TRIM,
                 low_water: int | None = None):
    """Cap history length at a clean turn boundary; evict old camera frames.

    Over `max_len`, cut down to `low_water` (default two thirds of max_len),
    NOT to max_len. Cutting to exactly the cap meant that once a conversation
    was long, EVERY turn went over by a message or two and was trimmed again --
    and a trim changes the front of every agent's prompt, so all three
    llama.cpp caches were lost on every single turn (2026-10-02: history
    pinned at 46-48 messages, the warmer re-reading ~3.5k tokens per agent
    after each turn and never finishing before the next one; "tell me a joke"
    1.4 s fresh, 5-13 s in a long conversation). With a low-water mark the
    reset happens once per several turns, and the turns between stay warm.

    Returns (messages, changed). `changed` means the cached prompt prefix was
    reset — the caller should background-warm the llama.cpp slot so the
    re-prefill happens while the robot is idle instead of on the next turn.
    """
    msgs = list(messages)
    if len(msgs) <= max_len:
        return msgs, False

    target = max_len * 2 // 3 if low_water is None else min(low_water, max_len)
    cut = len(msgs) - target
    while cut < len(msgs) and not (
        isinstance(msgs[cut], HumanMessage) and not is_camera_frame(msgs[cut])
    ):
        cut += 1
    if cut >= len(msgs):
        return msgs, False
    msgs = msgs[cut:]

    # The suffix re-prefills anyway — evict all but the newest frames now.
    frame_idxs = [i for i, m in enumerate(msgs) if has_image(m)]
    evict = frame_idxs[:-keep_frames] if keep_frames > 0 else frame_idxs
    for i in evict:
        msgs[i] = _evict_frame(msgs[i])

    return msgs, True
