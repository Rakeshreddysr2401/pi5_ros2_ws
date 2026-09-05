"""studio_voice_node — voice I/O for dev mode (`langgraph dev` / LangGraph Studio).

Production runs the graph in-process: agent_node owns the input queue, builds
the graph and publishes the reply. Dev mode moves the graph into the
`langgraph dev` server, which has no ROS side at all — so voice loses both its
ear and its mouth. This node is the missing pair, and nothing else:

    /voice/user_input ──► queue ──► StudioClient (HTTP :2024) ──► SentenceEmitter
                                          ▲                             │
    Studio browser box ──► server run ────┘ (watcher)     /voice/robot_speech + <|eou|>

Both directions end at the same speaker: an utterance spoken into the mic runs
on the bridge's thread (and so appears in the Studio UI), and a turn typed into
the Studio box is picked up by the watcher and spoken. One speech lock keeps
the two from interleaving mid-sentence.

What it deliberately does NOT do
--------------------------------
* No graph, no LLM config, no bridge. The `langgraph dev` process owns those
  (graph_studio.py attaches a real ROS2Bridge when ROS is sourced), so movement
  tools still publish /cmd_vel from THAT process. Two processes, no overlap:
  this one only reads /voice/user_input and writes /voice/robot_speech.
* No history list. The server thread holds the conversation (checkpointer), so
  history and trimming are its problem, not ours.
* No [SYSTEM] turns, no Telegram, no fast path. Those are agent_node features;
  dev mode is for stepping through the graph, and duplicating them here would
  give two implementations to keep in sync.

Run it alongside `./scripts/dev.sh` and the pi5 voice pair — never alongside
langrobo-brain, which owns the same topics.
"""

import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from langrobo_core.services.studio import (
    DEFAULT_ASSISTANT,
    DEFAULT_SKIP_NODES,
    DEFAULT_URL,
    StudioClient,
    StudioConfig,
    Token,
    reply_from_state,
    TurnError,
    Values,
)
from langrobo_core.utils.speech_stream import SentenceEmitter

# Spoken when the dev server is down or drops a turn. Short on purpose: the
# user is standing there and the real diagnosis is on the terminal.
OFFLINE_REPLY = "The dev server isn't answering. Check the langgraph dev terminal."


class StudioVoiceNode(Node):

    def __init__(self):
        super().__init__("studio_voice_node")

        # ── Parameters ────────────────────────────────────────────────────
        self.declare_parameter("studio_url",     DEFAULT_URL)
        self.declare_parameter("assistant_id",   DEFAULT_ASSISTANT)
        # persistent: follow-ups keep context (normal conversation).
        # per_turn:   fresh thread each utterance — for A/B-ing one prompt.
        self.declare_parameter("thread_mode",    "persistent")
        self.declare_parameter("turn_timeout_s", 180.0)
        # Nodes whose tokens never reach the speaker. The supervisor emits only
        # grammar-forced handovers; agent_node silences it with a per-agent
        # streaming=False override that the server has no equivalent for.
        self.declare_parameter("skip_nodes",     list(DEFAULT_SKIP_NODES))
        # False = speak the whole reply once at the end (parity with
        # stream_speech:=false on agent_node).
        self.declare_parameter("stream_speech",  True)
        self.declare_parameter("speak_errors",   True)
        # Speak turns started elsewhere — the Studio browser box, another
        # client. Off = the speaker only ever answers the mic.
        self.declare_parameter("watch_ui",       True)
        self.declare_parameter("watch_poll_s",   0.5)
        # Pin the bridge to an existing conversation (paste the thread id from
        # the Studio URL) so typed and spoken turns share one history.
        self.declare_parameter("thread_id",      "")

        cfg = StudioConfig(
            url=self.get_parameter("studio_url").value,
            assistant_id=self.get_parameter("assistant_id").value,
            thread_mode=self.get_parameter("thread_mode").value,
            turn_timeout_s=float(self.get_parameter("turn_timeout_s").value),
            skip_nodes=tuple(self.get_parameter("skip_nodes").value),
        )
        self._stream_speech = self.get_parameter("stream_speech").value
        self._speak_errors  = self.get_parameter("speak_errors").value
        self._client = StudioClient(cfg)
        pinned = str(self.get_parameter("thread_id").value or "").strip()
        if pinned:
            self._client.pin_thread(pinned)

        # ── Input queue ───────────────────────────────────────────────────
        # Same two-slot discipline as agent_node: one pending utterance, newer
        # input replaces it, and a barge-in flag abandons the turn in flight.
        # Deliberately smaller — no system or Telegram queues in dev mode.
        self._queue_lock     = threading.Lock()
        self._user_pending: str | None = None
        self._input_event    = threading.Event()
        self._turn_interrupt = threading.Event()
        self._turn_active    = False
        self._sticky_agent: str | None = None
        # One utterance at a time: the mic path and the watcher path share the
        # speaker, and half of each reply interleaved would be unintelligible.
        self._speech_lock    = threading.Lock()

        # ── Topics (the shared /voice contract — see PI5_VOICE.md) ────────
        self._speech_pub   = self.create_publisher(String, "/voice/robot_speech", 10)
        self._pub_thinking = self.create_publisher(Bool, "/brain/thinking", 1)
        self.create_subscription(String, "/voice/user_input", self._on_user_input, 10)
        self.create_subscription(String, "/voice/tts_stop",   self._on_tts_stop, 10)

        threading.Thread(target=self._worker_loop, daemon=True).start()
        if self.get_parameter("watch_ui").value:
            threading.Thread(target=self._watch_loop, daemon=True).start()

        self.get_logger().info(
            f"Studio voice bridge ready — {cfg.url} "
            f"(assistant={cfg.assistant_id}, threads={cfg.thread_mode}, "
            f"stream={self._stream_speech}, "
            f"watch_ui={self.get_parameter('watch_ui').value})")

    # ── Spin thread ───────────────────────────────────────────────────────

    def _on_user_input(self, msg: String) -> None:
        text = msg.data.strip()
        if not text:
            return
        with self._queue_lock:
            if self._user_pending is not None:
                self.get_logger().warning("Replacing an unanswered utterance")
            self._user_pending = text
            if self._turn_active:
                self._turn_interrupt.set()
        self._input_event.set()

    def _on_tts_stop(self, msg: String) -> None:
        """Stop keyword during speech. Wake-word barge-in ("[wake:…]") is the
        Jetson/tts_node's business and must not abandon the turn here."""
        if msg.data.startswith("[wake:"):
            return
        with self._queue_lock:
            if self._turn_active:
                self._turn_interrupt.set()

    # ── Worker thread ─────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while True:
            self._input_event.wait()
            with self._queue_lock:
                text = self._user_pending
                self._user_pending = None
                self._input_event.clear()
                self._turn_interrupt.clear()
                self._turn_active = text is not None
            if text:
                try:
                    with self._speech_lock:
                        self._run_turn(text)
                except Exception as exc:            # never kill the worker
                    self.get_logger().error(f"Turn failed: {exc}")
                    if self._speak_errors:
                        self._speak_whole(OFFLINE_REPLY)
                finally:
                    with self._queue_lock:
                        self._turn_active = False
                    self._pub_thinking.publish(Bool(data=False))

    def _run_turn(self, text: str) -> None:
        self.get_logger().info(f"→ Studio: {text}")
        self._pub_thinking.publish(Bool(data=True))

        emitter = SentenceEmitter(self._publish_chunk) if self._stream_speech else None
        collected: list[str] = []
        final_state: dict | None = None
        error: str | None = None

        for event in self._client.stream_turn(
                text,
                active_agent=self._sticky_agent or "chat",
                interrupted=self._turn_interrupt.is_set):
            if isinstance(event, Token):
                collected.append(event.text)
                if emitter:
                    emitter.feed(event.text)
            elif isinstance(event, Values):
                final_state = event.state
            elif isinstance(event, TurnError):
                error = event.message
                self.get_logger().warning(f"Studio turn error: {error}")
                break

        if self._turn_interrupt.is_set():
            # Barge-in: close any partial utterance so the mic is released, then
            # leave sticky routing untouched — an abandoned turn changes nothing.
            if emitter:
                emitter.close()
            self.get_logger().info("Turn abandoned — newer utterance")
            return

        if error is not None:
            if emitter:
                emitter.close()
            if self._speak_errors:
                self._speak_whole(OFFLINE_REPLY)
            return

        reply = "".join(collected).strip() or (reply_from_state(final_state) or "")
        if emitter:
            spoke = emitter.close()
            if not spoke and reply:
                # Nothing crossed a sentence boundary (rare: a one-word reply
                # shorter than the minimum chunk) — say it whole.
                self._speak_whole(reply)
        elif reply:
            self._speak_whole(reply)

        if reply:
            self.get_logger().info(f"← {reply[:160]}")
        else:
            self.get_logger().warning("Empty reply — nothing spoken")

        self._remember_sticky(final_state)

    # ── Watcher thread: speak turns started in the Studio UI ──────────────

    def _watch_loop(self) -> None:
        poll = float(self.get_parameter("watch_poll_s").value)
        for thread_id, run_id in self._client.iter_new_runs(poll_s=poll):
            try:
                self._speak_run(thread_id, run_id)
            except Exception as exc:                 # never kill the watcher
                self.get_logger().warning(f"Watched run {run_id} failed: {exc}")

    def _speak_run(self, thread_id: str, run_id: str) -> None:
        """Attach to a run somebody else started and speak its reply.

        The mic keeps priority: if a spoken turn is in flight we wait for the
        lock rather than talking over it, and a barge-in still only cancels the
        mic's own run (this one belongs to whoever typed it).
        """
        self.get_logger().info(f"Watching run {run_id[:8]} on thread {thread_id[:8]}")
        with self._speech_lock:
            collected: list[str] = []
            final_state: dict | None = None
            for event in self._client.join_run(thread_id, run_id):
                if isinstance(event, Token):
                    collected.append(event.text)
                elif isinstance(event, Values):
                    final_state = event.state
                elif isinstance(event, TurnError):
                    self.get_logger().warning(f"Watched run error: {event.message}")
                    break
            # Tokens are not replayed to a joiner, so the state snapshot is the
            # normal source here; tokens stay the preferred one if a future
            # server version does replay them.
            reply = "".join(collected).strip() or (reply_from_state(final_state) or "")
            if reply:
                self._speak_whole(reply)
                self.get_logger().info(f"← (studio) {reply[:160]}")
            else:
                self.get_logger().info("Watched run produced no text")

    def _remember_sticky(self, state: dict | None) -> None:
        """Carry the ending agent into the next turn, minus the supervisor.

        Same rule as agent_node: a specialist handing back leaves
        active_agent='supervisor', and re-entering there costs a routing hop.
        """
        agent = (state or {}).get("active_agent")
        self._sticky_agent = None if agent == "supervisor" else agent

    # ── Speech publishing (the /voice/* wire contract) ────────────────────

    def _publish_chunk(self, text: str) -> None:
        self._speech_pub.publish(String(data=text))

    def _speak_whole(self, text: str) -> None:
        """One complete utterance: the text, then the end-of-utterance marker."""
        emitter = SentenceEmitter(self._publish_chunk)
        emitter.feed(text)
        emitter.close(force=True)


def main(args=None):
    rclpy.init(args=args)
    node = StudioVoiceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
