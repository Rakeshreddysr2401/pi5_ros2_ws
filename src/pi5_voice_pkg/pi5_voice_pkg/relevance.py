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

SYSTEM = ("You filter a living-room microphone for Kino, a home robot assistant "
          ""
          "Speech is translated from Telugu. Decide if the sentence is directed AT THE ROBOT: "
          "a question or request to an assistant, a command, or an answer to what the robot just said. "
          "Answer no for people talking to each other, TV or video audio, songs, and bare reactions "
          "or fragments ('What?', 'Okay, tell me', 'Yes', 'It is happening', 'Put it below') -- "
          "unless they answer a question the robot just asked. A real request names what it "
          "wants: a question, an action, music (play, next, pause, stop, volume), a timer, a list. "
          "Answer only yes or no.")
GRAMMAR = 'root ::= "yes" | "no"'


# THE FILTER'S OWN KV SLOT (2026-10-10). Unpinned, llama.cpp put this request
# in whichever slot was free -- measured: slot 3, the vision-tool slot, held
# this 205-token prompt instead of the photo log, so every "not for me" (about
# 60 that evening, TV and room talk) threw the photos out and the next photo
# question re-read all of them; and with all four slots busy during a search
# it waited out its 3 s and dropped the sentence ("llm unreachable", 7 times
# in 3 minutes). Slots 0-3 are the brain's (registry.py, CLAUDE.md rule 2);
# the filter gets slot 4 -- when the server has one (llama-server
# --parallel 5). stt_node checks /props and pins only then.
ADDRESSING_SLOT = 4


def request_body(heard: str, robot_last: str = "", context: str = "",
                 slot: int | None = None) -> dict:
    """context: what is going on ("[music playing: Kesariya]"). slot: the KV
    slot to pin (id_slot), or None to let the server choose."""
    user = (f"{context}\n" if context else "") + \
        (f"Robot's last words: {robot_last!r}\n" if robot_last else "") + \
        f"Heard: {heard!r}\nDirected at the robot?"
    body = {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "max_tokens": 2, "temperature": 0, "grammar": GRAMMAR, "cache_prompt": True}
    if slot is not None:
        body["id_slot"] = int(slot)
    return body


def props_url(chat_url: str) -> str:
    """http://host:8080/v1/chat/completions -> http://host:8080/props"""
    base = re.sub(r"/v1/.*$", "", chat_url.rstrip("/"))
    return base + "/props"


def server_slots(chat_url: str, timeout: float = 3.0) -> int | None:
    """The llama.cpp server's slot count (GET /props total_slots), or None."""
    try:
        with urllib.request.urlopen(props_url(chat_url), timeout=timeout) as r:
            n = json.load(r).get("total_slots")
            return int(n) if n is not None else None
    except Exception:
        return None


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
                 context: str = "", slot: int | None = None) -> bool | None:
    """True / False, or None when the LLM could not be asked (caller decides)."""
    try:
        req = urllib.request.Request(url, json.dumps(request_body(heard, robot_last, context, slot)).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse(json.load(r))
    except Exception:
        return None


class HoldWhileBusy:
    """An overheard sentence (no name; only this filter said "for me") waits
    while the brain is busy with a turn, and goes on when the turn ends.

    2026-10-11, twice in one evening: "Do you have an audio?" (room talk) came
    3 s after "Kino, follow me", barged in and cancelled the follow before its
    tool ran; earlier "How do you mean?" replaced "move back 40 cm". A sentence
    WITH the name, and a stop word, still cut in at once -- the caller only
    offers the llm-judged ones. Held one at a time (the newest wins, like the
    brain's own queue) and dropped when older than max_age_s. Pure: no clock
    or ROS of its own.
    """

    def __init__(self, max_age_s: float = 20.0, max_busy_s: float = 300.0):
        self.max_age_s = max_age_s
        self.max_busy_s = max_busy_s     # a brain that died mid-turn never says "idle"
        self.busy = False
        self._busy_since = 0.0
        self._held: tuple[str, float] | None = None

    def offer(self, text: str, now: float) -> bool:
        """True = held (the brain is busy); False = send it now."""
        if not self.busy or now - self._busy_since > self.max_busy_s:
            return False
        self._held = (text, now)
        return True

    def set_busy(self, busy: bool, now: float) -> str | None:
        """The brain's /brain/thinking. On busy -> idle: the held sentence to
        send now, or None (nothing held, or it went stale)."""
        if busy and not self.busy:
            self._busy_since = now
        self.busy = busy
        if busy or self._held is None:
            return None
        text, at = self._held
        self._held = None
        return text if now - at <= self.max_age_s else None
