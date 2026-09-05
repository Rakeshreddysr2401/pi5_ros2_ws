"""Dev-mode Studio client tests: token de-duplication across both server wire
shapes, node filtering, degradation when the server is down, and the sentence
emitter's utterance contract. No server, no ROS, no network."""

import pytest

from langrobo_core.services.studio import (
    StudioClient,
    StudioConfig,
    Token,
    TurnError,
    Values,
    reply_from_state,
    _iter_messages,
    _TextTracker,
)
from langrobo_core.utils.speech_stream import SPEECH_EOU, SentenceEmitter


# ── Fake transport ───────────────────────────────────────────────────────────

class _Part:
    def __init__(self, event, data):
        self.event, self.data = event, data


class _FakeRuns:
    def __init__(self, parts, fail=None, listing=()):
        self._parts, self._fail = parts, fail
        self.cancelled = []
        self.listing = list(listing)
        self.joined = []

    def list(self, thread_id, limit=10, **kw):
        return self.listing

    def join_stream(self, thread_id, run_id, stream_mode=None, **kw):
        self.joined.append(run_id)
        return iter(self._parts)

    def stream(self, thread_id, assistant_id, input=None, stream_mode=None):
        if self._fail:
            raise self._fail
        self.last_input = input
        return iter(self._parts)

    def cancel(self, thread_id, run_id):
        self.cancelled.append(run_id)


class _FakeThreads:
    def __init__(self, fail=False):
        self._fail, self.created = fail, 0

    def create(self):
        if self._fail:
            raise ConnectionError("connection refused")
        self.created += 1
        return {"thread_id": f"t{self.created}"}


class _FakeClient:
    def __init__(self, parts=(), thread_fail=False, run_fail=None,
                 busy=(), listing=()):
        self.threads = _FakeThreads(thread_fail)
        self.threads.search = lambda **kw: list(busy)
        self.runs = _FakeRuns(list(parts), run_fail, listing)


def _client(parts=(), **kw):
    fake = _FakeClient(parts, **kw)
    return StudioClient(StudioConfig(), client_factory=lambda: fake), fake


# ── Token de-duplication ─────────────────────────────────────────────────────

def test_cumulative_chunks_emit_only_the_suffix():
    t = _TextTracker()
    assert t.delta("a", "Hello") == "Hello"
    assert t.delta("a", "Hello there") == " there"
    assert t.delta("a", "Hello there.") == "."


def test_delta_chunks_pass_through_whole():
    t = _TextTracker()
    assert t.delta("a", "Hello") == "Hello"
    assert t.delta("a", " there") == " there"
    assert t.delta("a", ".") == "."


def test_repeated_identical_chunk_emits_nothing():
    t = _TextTracker()
    t.delta("a", "Hi")
    assert t.delta("a", "Hi") == ""


def test_two_messages_track_independently():
    t = _TextTracker()
    assert t.delta("a", "one") == "one"
    assert t.delta("b", "two") == "two"
    assert t.delta("a", "one!") == "!"


# ── Wire-shape tolerance ─────────────────────────────────────────────────────

@pytest.mark.parametrize("data,expect", [
    ({"content": "hi", "type": "ai"}, 1),                             # single
    ([{"content": "hi", "type": "ai"}, {"langgraph_node": "chat"}], 1),  # tuple
    ([{"content": "a", "type": "ai"}, {"content": "b", "type": "ai"}], 2),  # partial
    ([], 0),
    ("nonsense", 0),
])
def test_iter_messages_accepts_every_shape(data, expect):
    assert len(list(_iter_messages(data))) == expect


def test_tuple_shape_carries_metadata():
    (_msg, meta), = _iter_messages(
        [{"content": "hi", "type": "ai"}, {"langgraph_node": "chat"}])
    assert meta["langgraph_node"] == "chat"


# ── Turn streaming ───────────────────────────────────────────────────────────

def _msg(content, node="chat", mid="m1", mtype="ai"):
    return _Part("messages", [{"id": mid, "content": content, "type": mtype},
                              {"langgraph_node": node}])


def test_stream_turn_yields_tokens_then_values():
    client, _ = _client([
        _msg("Going"), _msg("Going to the kitchen."),
        _Part("values", {"active_agent": "navigate"}),
    ])
    events = list(client.stream_turn("go to the kitchen"))
    assert [e.text for e in events if isinstance(e, Token)] == ["Going", " to the kitchen."]
    assert [e.state for e in events if isinstance(e, Values)] == [{"active_agent": "navigate"}]


def test_skipped_nodes_are_never_spoken():
    client, _ = _client([_msg("routing", node="supervisor"), _msg("Hello", node="chat")])
    assert [e.text for e in list(client.stream_turn("hi")) if isinstance(e, Token)] == ["Hello"]


def test_tool_and_human_messages_are_not_spoken():
    client, _ = _client([_msg("tool output", mtype="tool"), _msg("Done.", mid="m2")])
    assert [e.text for e in list(client.stream_turn("x")) if isinstance(e, Token)] == ["Done."]


def test_sends_only_the_new_message_server_holds_history():
    client, fake = _client([_Part("values", {})])
    list(client.stream_turn("hello", active_agent="chat"))
    assert fake.runs.last_input["messages"] == [{"role": "user", "content": "hello"}]
    assert fake.runs.last_input["active_agent"] == "chat"


def test_persistent_mode_reuses_one_thread():
    client, fake = _client([_Part("values", {})])
    list(client.stream_turn("one"))
    list(client.stream_turn("two"))
    assert fake.threads.created == 1


def test_per_turn_mode_makes_a_new_thread_each_time():
    fake = _FakeClient([_Part("values", {})])
    client = StudioClient(StudioConfig(thread_mode="per_turn"), client_factory=lambda: fake)
    list(client.stream_turn("one"))
    fake.runs._parts = [_Part("values", {})]
    list(client.stream_turn("two"))
    assert fake.threads.created == 2


# ── Degradation (hard rule 4) ────────────────────────────────────────────────

def test_server_down_yields_error_not_exception():
    client, _ = _client(thread_fail=True)
    events = list(client.stream_turn("hello"))
    assert len(events) == 1 and isinstance(events[0], TurnError)
    assert events[0].recoverable is False


def test_mid_run_failure_yields_error():
    client, _ = _client(run_fail=RuntimeError("stream closed"))
    events = list(client.stream_turn("hello"))
    assert isinstance(events[-1], TurnError)


def test_server_error_event_ends_the_turn():
    client, _ = _client([_Part("error", {"message": "boom"}), _msg("never spoken")])
    events = list(client.stream_turn("hello"))
    assert isinstance(events[-1], TurnError) and "boom" in events[-1].message
    assert not [e for e in events if isinstance(e, Token)]


def test_barge_in_stops_and_cancels_the_run():
    client, fake = _client([
        _Part("metadata", {"run_id": "r9"}), _msg("Going"), _msg("Going on and on."),
    ])
    seen = {"n": 0}

    def interrupted():
        seen["n"] += 1
        return seen["n"] > 2        # abandon after the first token

    tokens = [e.text for e in client.stream_turn("hi", interrupted=interrupted)
              if isinstance(e, Token)]
    assert tokens == ["Going"]
    assert fake.runs.cancelled == ["r9"]


# ── Utterance contract ───────────────────────────────────────────────────────

def test_emitter_chunks_on_sentences_and_closes_with_eou():
    out = []
    e = SentenceEmitter(out.append)
    e.feed("Going to the kitchen. ")
    assert out == ["Going to the kitchen."]
    e.feed("Almost there now.")
    e.close()
    assert out == ["Going to the kitchen.", "Almost there now.", SPEECH_EOU]


def test_emitter_stays_silent_for_a_turn_with_no_text():
    out = []
    assert SentenceEmitter(out.append).close() is False
    assert out == []


def test_emitter_force_closes_a_muted_utterance():
    out = []
    SentenceEmitter(out.append).close(force=True)
    assert out == [SPEECH_EOU]


# ── Watching runs started elsewhere (the Studio browser box) ────────────────

def _stop_after(n):
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > n
    return stop


def test_watcher_yields_a_run_started_in_the_ui():
    fake = _FakeClient(busy=[{"thread_id": "tA"}],
                       listing=[{"run_id": "r1", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    found = list(client.iter_new_runs(poll_s=0, stop=_stop_after(1),
                                      skip_existing=False))
    assert found == [("tA", "r1")]


def test_watcher_never_repeats_a_run():
    fake = _FakeClient(busy=[{"thread_id": "tA"}],
                       listing=[{"run_id": "r1", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    assert len(list(client.iter_new_runs(poll_s=0, stop=_stop_after(3),
                                         skip_existing=False))) == 1


def test_watcher_skips_finished_runs():
    fake = _FakeClient(busy=[{"thread_id": "tA"}],
                       listing=[{"run_id": "r1", "status": "success"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(1))) == []


def test_watcher_skips_our_own_voice_run():
    """The mic path must not be spoken twice — once driven, once watched."""
    fake = _FakeClient([_Part("metadata", {"run_id": "rOWN"}), _Part("values", {})],
                       busy=[{"thread_id": "tA"}],
                       listing=[{"run_id": "rOWN", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    list(client.stream_turn("hello"))          # registers rOWN as ours
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(1))) == []


def test_join_run_speaks_a_ui_turn():
    fake = _FakeClient([_msg("Typed reply.", mid="u1"), _Part("values", {})])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    events = list(client.join_run("tA", "r1"))
    assert [e.text for e in events if isinstance(e, Token)] == ["Typed reply."]
    assert fake.runs.joined == ["r1"]


def test_join_run_failure_degrades():
    fake = _FakeClient()
    fake.runs.join_stream = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gone"))
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    assert isinstance(list(client.join_run("tA", "r1"))[0], TurnError)


def test_pin_thread_uses_an_existing_conversation():
    fake = _FakeClient([_Part("values", {})])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    client.pin_thread("tUI")
    list(client.stream_turn("hi"))
    assert fake.threads.created == 0
    assert client.status()["thread_id"] == "tUI"


# ── Reply extraction from a values snapshot (the watch path's source) ───────

def test_reply_from_state_takes_the_last_ai_text():
    state = {"messages": [
        {"type": "human", "content": "hi"},
        {"type": "ai", "content": "Hello there."},
    ]}
    assert reply_from_state(state) == "Hello there."


def test_reply_from_state_skips_tool_messages():
    state = {"messages": [
        {"type": "human", "content": "battery?"},
        {"type": "ai", "content": ""},
        {"type": "tool", "content": "87%"},
        {"type": "ai", "content": "Battery is at 87 percent."},
    ]}
    assert reply_from_state(state) == "Battery is at 87 percent."


def test_reply_from_state_stops_at_the_human_turn():
    """A turn with no AI text must not re-speak the previous turn's reply."""
    state = {"messages": [
        {"type": "ai", "content": "Previous answer."},
        {"type": "human", "content": "new question"},
    ]}
    assert reply_from_state(state) is None


def test_reply_from_state_handles_block_content():
    state = {"messages": [
        {"type": "human", "content": "hi"},
        {"type": "ai", "content": [{"type": "text", "text": "Block reply."}]},
    ]}
    assert reply_from_state(state) == "Block reply."


def test_reply_from_state_tolerates_empty_input():
    assert reply_from_state(None) is None
    assert reply_from_state({}) is None


# ── The watcher must never grab a run we are driving (race found live) ──────

def test_watcher_skips_our_own_thread_while_driving():
    """`_own_runs` fills only when the metadata event lands — the poll can beat
    it. The thread guard has to hold before any run id is known."""
    fake = _FakeClient(listing=[{"run_id": "rMIC", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    client.pin_thread("tOWN")
    fake.threads.search = lambda **kw: [{"thread_id": "tOWN"}]

    turn = client.stream_turn("hello")          # generator: not started yet
    next(turn, None)                            # enter it — driving begins
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(1))) == []


def test_watcher_skips_our_thread_briefly_after_the_turn():
    fake = _FakeClient([_Part("values", {})],
                       listing=[{"run_id": "rMIC", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    client.pin_thread("tOWN")
    fake.threads.search = lambda **kw: [{"thread_id": "tOWN"}]
    list(client.stream_turn("hello"))           # drive to completion
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(1))) == []


def test_watcher_still_sees_other_threads_while_we_drive():
    fake = _FakeClient(listing=[{"run_id": "rUI", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    client.pin_thread("tOWN")
    fake.threads.search = lambda **kw: [{"thread_id": "tOTHER"}]
    turn = client.stream_turn("hello")
    next(turn, None)
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(1),
                                     skip_existing=False)) == [("tOTHER", "rUI")]


def test_watcher_ignores_runs_already_in_flight_at_startup():
    """A run left over from the previous session must not be spoken into an
    empty room after a restart (observed live 2026-09-05)."""
    fake = _FakeClient(busy=[{"thread_id": "tA"}],
                       listing=[{"run_id": "rOLD", "status": "running"}])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(2))) == []


def test_watcher_still_reports_runs_that_start_after_it_does():
    fake = _FakeClient(listing=[])
    client = StudioClient(StudioConfig(), client_factory=lambda: fake)
    polls = {"n": 0}

    def search(**kw):
        polls["n"] += 1
        if polls["n"] >= 2:      # the run starts after the warm-up pass
            fake.runs.listing = [{"run_id": "rNEW", "status": "running"}]
        return [{"thread_id": "tA"}]

    fake.threads.search = search
    assert list(client.iter_new_runs(poll_s=0, stop=_stop_after(3))) == [("tA", "rNEW")]
