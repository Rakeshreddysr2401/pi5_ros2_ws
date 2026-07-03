"""Streaming speech — pure zone (no rclpy).

Turns LLM token streams into sentence-sized TTS chunks. agent_node attaches a
SpeechStreamHandler per turn; every streamed content token from any agent flows
through it, complete sentences are published immediately on /voice/robot_speech,
and the Jetson tts_node synthesises them while the LLM is still generating.

Protocol (String topic unchanged): each sentence is one message; the utterance
is terminated by a message whose data is exactly SPEECH_EOU. The Jetson holds
/voice/tts_speaking True from the first chunk until the marker, so the mic
stays muted across chunk gaps.

Text that accompanies a tool call streams too — that is deliberate: the model's
"Let me check." becomes the acknowledgement the user hears while the tool runs.
"""

import logging
import re
from typing import Callable
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# End-of-utterance marker message. Must match tts_node.py on the Jetson.
SPEECH_EOU = "<|eou|>"

# Sentence boundary: terminal punctuation (plus closing quotes/brackets)
# followed by whitespace, or a bare newline (list items, headings).
_BOUNDARY = re.compile(r"[.!?…]+[\"'')\]]*\s|\n")

# Trailing words that end with a period but do not end a sentence.
_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc",
    "e.g", "i.e", "eg", "ie", "no", "approx", "dept", "fig", "min", "max",
}

# Chunks shorter than this merge into the next sentence (avoids "1." / "Hi.").
_MIN_CHUNK_CHARS = 12

# Safety valve: a run this long with no boundary gets cut at the last space,
# so a punctuation-free ramble still starts speaking promptly.
_MAX_BUFFER_CHARS = 250


def _norm(text: str) -> str:
    return " ".join(text.split())


def _ends_with_abbreviation(candidate: str) -> bool:
    tail = re.search(r"(\S+)\s*$", candidate)
    if not tail:
        return False
    word = tail.group(1).rstrip(".!?…\"')]").lstrip("(\"'").lower()
    # Single letters are initials ("J. Smith"); known abbreviations never
    # terminate a sentence.
    return (len(word) == 1 and word.isalpha()) or word in _ABBREVIATIONS


def split_sentences(buf: str) -> tuple[list[str], str]:
    """Split off complete sentences; return (sentences, remainder).

    The remainder is the trailing text that may still be growing — the caller
    keeps it buffered until more tokens (or end of generation) arrive.
    """
    out: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(buf):
        candidate = buf[start:m.end()].strip()
        if not candidate:
            start = m.end()
            continue
        # Bare newlines always break; periods must not follow an abbreviation.
        if m.group() != "\n" and _ends_with_abbreviation(buf[start:m.end()]):
            continue
        if len(candidate) < _MIN_CHUNK_CHARS:
            continue  # too short to speak alone — merge into the next sentence
        out.append(candidate)
        start = m.end()

    rest = buf[start:]
    if len(rest) > _MAX_BUFFER_CHARS:
        cut = rest.rfind(" ", _MIN_CHUNK_CHARS, _MAX_BUFFER_CHARS)
        if cut > 0:
            out.append(rest[:cut].strip())
            rest = rest[cut + 1:]
    return out, rest


class SpeechStreamHandler(BaseCallbackHandler):
    """Publishes sentence chunks as LLM tokens arrive.

    Create one per turn and pass it in the graph config alongside the timing
    handler. Only agents whose LLM has streaming enabled fire tokens; the
    supervisor streams nothing (streaming disabled per-agent) and non-streaming
    fallbacks are covered by agent_node publishing the full text when spoke()
    returns False.
    """

    raise_error = False

    def __init__(self, publish: Callable[[str], None]):
        self._publish = publish
        self._buf:  dict[UUID, str] = {}   # unfinished sentence per LLM run
        self._full: dict[UUID, str] = {}   # everything streamed per LLM run
        self._completed: list[str] = []    # normalized text of finished runs
        self.chunks_sent = 0

    # ── LangChain callbacks ────────────────────────────────────────────────

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._buf[run_id] = ""
        self._full[run_id] = ""

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        if not isinstance(token, str) or not token or run_id not in self._buf:
            return
        self._full[run_id] += token
        ready, rest = split_sentences(self._buf[run_id] + token)
        for sentence in ready:
            self._send(sentence)
        self._buf[run_id] = rest

    def on_llm_end(self, response, *, run_id, **kwargs):
        rest = self._buf.pop(run_id, "").strip()
        if rest:
            self._send(rest)
        full = self._full.pop(run_id, "")
        if full.strip():
            self._completed.append(_norm(full))

    def on_llm_error(self, error, *, run_id, **kwargs):
        # Drop the partial sentence — agent_node speaks the fallback message.
        self._buf.pop(run_id, None)
        self._full.pop(run_id, None)

    # ── Turn-level API (agent_node) ────────────────────────────────────────

    def spoke(self, text: str | None) -> bool:
        """True if `text` was already fully streamed to TTS this turn."""
        return bool(text) and _norm(text) in self._completed

    def _send(self, text: str) -> None:
        try:
            self._publish(text)
            self.chunks_sent += 1
        except Exception:
            logger.exception("speech chunk publish failed")
