"""Was a spoken turn said TO the robot? -- pure zone.

WHY (2026-10-13). With the "is this for me?" pre-filter off (owner,
2026-10-12), every sentence the mic hears reaches an agent, and the agent may
answer [silent]. Measured on the Mac with chat's real prompt: it stayed silent
for 3 of 6 room sentences, and "Amma, dinner is ready, come and eat" made it
SEND A TELEGRAM MESSAGE. A prompt rule is a thing to measure, not a fix; this is
the fix for the part that matters -- side effects.

A turn counts as addressed when the name is in it (any misspelling the
recogniser makes of "Mitra": Emitra, Imitra, Mithra), or the robot spoke within
FOLLOW_UP_S -- the person is answering it ("yes, send it"). Telegram and
[SYSTEM] turns always are. This is about WHO, never WHAT: no intent words here.

Tools that change the world beyond the room (send_telegram_*, memory
remember/forget) refuse on an unaddressed voice turn and tell the model to ask.
Driving is not gated here: "turn left" with no name is the normal way to talk
to it, and stopping must never wait on this.
"""

NAME_FORMS = ("mitra", "mithra", "mittra", "metra")
FOLLOW_UP_S = 20.0


def name_said(text: str) -> bool:
    low = (text or "").lower()
    return any(n in low for n in NAME_FORMS)


def addressed(text: str, channel: str, last_spoke_ts: float | None, now: float) -> bool:
    if channel != "voice":
        return True
    if name_said(text):
        return True
    return last_spoke_ts is not None and now - last_spoke_ts <= FOLLOW_UP_S


def not_addressed(state: dict | None) -> bool:
    """For a tool: True only when agent_node marked this turn as NOT said to
    the robot. Unset (Studio, the twin, tests) means addressed."""
    return (state or {}).get("addressed") is False


def refusal(what: str) -> str:
    return (f"NOT done -- nothing was {what}: your name was not said and you had not "
            "just spoken, so this may have been room talk, not a request to you. If "
            "it was clearly not for you, reply with exactly [silent]. Otherwise ask "
            "one short question to check, like \"Should I do that?\"")
