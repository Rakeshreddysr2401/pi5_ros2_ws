"""Is a heard sentence meant for the robot? -- addressing without a wake word.

Owner, 2026-10-04: "stop this wake word, it is messy; I can't tell whether it
processed or not". With no name required the mic still hears the TV and the
people in the room, and in open mode the robot answered all of it (a "book
test", a "red ball", a "movie tomorrow"). So every sentence without the name
is put to the brain's own LLM (Gemma on the Mac mini) as one grammar-forced
yes/no on a short prompt, with the robot's last words for context ("Five
minutes." after "For how long?" is an answer).

Measured the same morning on 18 sentences the mic really heard plus real
requests: 17/18 right, 0.67 s median (0.93 s max). The miss was a garbled
"Tell me a book test it gets for me." Pure: the caller does the HTTP.
"""

from __future__ import annotations

import json
import re
import urllib.request

SYSTEM = ("You filter a living-room microphone for Mitra, a home robot assistant "
          "(in Telugu 'mitra' means 'friend', so 'Friend, ...' is the robot being called). "
          "Speech is translated from Telugu. Decide if the sentence is directed AT THE ROBOT: "
          "a question or request to an assistant, a command, or an answer to what the robot just said. "
          "Answer no for people talking to each other, TV or video audio, songs, and bare reactions "
          "or fragments ('What?', 'Okay, tell me', 'Yes', 'It is happening', 'Put it below') -- "
          "unless they answer a question the robot just asked. A real request names what it "
          "wants: a question, an action, music (play, next, pause, stop, volume), a timer, a list. "
          "Answer only yes or no.")
GRAMMAR = 'root ::= "yes" | "no"'


def request_body(heard: str, robot_last: str = "", context: str = "") -> dict:
    """context: what is going on ("[music playing: Kesariya]")."""
    user = (f"{context}\n" if context else "") + \
        (f"Robot's last words: {robot_last!r}\n" if robot_last else "") + \
        f"Heard: {heard!r}\nDirected at the robot?"
    return {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "max_tokens": 2, "temperature": 0, "grammar": GRAMMAR, "cache_prompt": True}


def parse(response: dict) -> bool:
    """True only for a clear yes."""
    try:
        return response["choices"][0]["message"]["content"].strip().lower() == "yes"
    except (KeyError, IndexError, AttributeError, TypeError):
        return False


# While music plays, its controls are short and unambiguous -- and a one-word
# "Louder." was the filter's miss even with the music in context (2026-10-04).
# These are accepted at once, no LLM.
_MUSIC_CONTROL = re.compile(
    r"^\W*(?:please\s+)?(?:louder|quieter|softer|next(?:\s+(?:song|one|track))?|skip(?:\s+(?:it|this|song))?|"
    r"pause(?:\s+(?:it|the\s+music|music))?|resume|play\s+again|stop(?:\s+(?:it|the\s+music|music|the\s+song))?|"
    r"(?:turn\s+(?:it\s+)?)?volume\s+(?:up|down)|turn\s+(?:it\s+)?(?:up|down)|"
    r"(?:a\s+(?:little|bit)\s+)?(?:louder|quieter))(?:\s+please)?\W*$", re.IGNORECASE)


def music_control(heard: str) -> bool:
    """A bare music control ("Louder.", "Next song", "Pause it") -- for when music is playing."""
    return bool(_MUSIC_CONTROL.match(heard or ""))


def is_for_robot(heard: str, robot_last: str, url: str, timeout: float = 3.0,
                 context: str = "") -> bool | None:
    """True / False, or None when the LLM could not be asked (caller decides)."""
    try:
        req = urllib.request.Request(url, json.dumps(request_body(heard, robot_last, context)).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse(json.load(r))
    except Exception:
        return None
