"""Role → capability policy for remote senders. Pure zone.

One place decides who may do what through the robot. Telegram gives verified
identity per message (the chat_id allowlist in config maps id → name + role);
voice has NO speaker identity until P2 face/speaker recognition, so voice
turns run with owner capabilities (VOICE_ROLE) — documented trade-off, not an
oversight.

Enforcement lives in the TOOLS (they read the turn's sender_role from state
and refuse), never only in prompts — a prompt can be sweet-talked, an
`if cap not in role` cannot. Prompts add tone on top, not security.
"""

from __future__ import annotations

# Capabilities — what a sender may ask the robot to do.
CAP_CHAT = "chat"      # converse, ask questions
CAP_RELAY = "relay"    # send/relay messages to household members
CAP_PHOTO = "photo"    # receive camera photos (it's a camera inside the home)
CAP_MOVE = "move"      # drive the robot
CAP_ORDER = "order"    # place food orders (spends money)
CAP_REMIND = "remind"  # set/cancel reminders

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "owner":  frozenset({CAP_CHAT, CAP_RELAY, CAP_PHOTO, CAP_MOVE, CAP_ORDER, CAP_REMIND}),
    "family": frozenset({CAP_CHAT, CAP_RELAY, CAP_REMIND}),
    "guest":  frozenset({CAP_CHAT}),
}

ROLES = tuple(ROLE_CAPABILITIES)

# Voice turns carry no identity yet — they act as the owner.
VOICE_ROLE = "owner"


def has_capability(role: str, capability: str) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, frozenset())
