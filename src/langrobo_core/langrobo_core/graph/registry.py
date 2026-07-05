"""Central agent registry — single source of truth for agent names, descriptions, and examples.

Add a new agent here and both the supervisor prompt and handle_handover context
messages update automatically — no other files need touching.
"""

AGENTS: dict[str, dict] = {
    "chat": {
        "description": "general questions, web search, reminders and timers (setting, listing, announcing due ones), home watch mode (arm/disarm, announcing alerts), system status, small talk, anything not covered by other agents",
        "examples": ["what's the weather?", "tell me a joke", "remind me in 10 minutes", "watch the house", "[SYSTEM] Reminder due", "[SYSTEM] Watch alert", "[SYSTEM] … announce this aloud …"],
    },
    "local_agent": {
        "description": "anything about what the robot sees — scene description, object/person detection, visual queries, and follow-up questions about the same scene (reasons over the actual camera image and remembers it)",
        "examples": ["what do you see?", "is there anyone in the room?", "did he wear spectacles?", "is this the real poster?"],
    },
    "navigate": {
        "description": "moving the robot, going somewhere, finding and approaching objects, stopping; also owns follow-me/come-to-me requests (it explains person following isn't available yet)",
        "examples": ["go to the kitchen", "find the bottle", "come here", "follow me", "stop"],
    },
    "status": {
        "description": "robot battery level, hardware state, what the robot is currently doing",
        "examples": ["what's your battery?", "how are you doing?", "are you okay?"],
    },
    "swiggy": {
        "description": "food ordering, restaurant search, browsing menus, managing cart, placing orders",
        "examples": ["order pizza", "what restaurants are nearby?", "add to cart"],
    },
    "tracker": {
        "description": "checking delivery status, order ETA, tracking a Swiggy order",
        "examples": ["where's my order?", "how long until delivery?", "track my food"],
    },
}


def build_supervisor_agent_list() -> str:
    """Return the agents block for the supervisor prompt."""
    lines = []
    for name, meta in AGENTS.items():
        lines.append(f'- "{name}" : {meta["description"]}')
    return "\n".join(lines)
