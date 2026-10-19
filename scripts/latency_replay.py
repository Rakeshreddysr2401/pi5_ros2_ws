#!/usr/bin/env python3
"""Latency replay harness — per-turn stage waterfall for the voice pipeline.

Two modes:

  Replay (default): injects canned utterances into /voice/user_input (skipping
  the mic/STT stages) and prints a waterfall of /diag/timing events per turn.
  Run on the Pi5 (or any machine with ROS2 + DDS reach to the robot) while
  agent_node — and optionally the Jetson tts_node — is up.

      python3 scripts/latency_replay.py "hello robot" "what can you do"

  Listen (--listen): injects nothing; passively groups timing events into turns
  as you talk to the robot with the real mic. This is the only mode that shows
  the STT stages (stt_vad_end / stt_end).

      python3 scripts/latency_replay.py --listen

Stages (emitted by agent_node/ros2_bridge, stt_node, tts_node):
  stt_vad_end → stt_end → brain_receive → graph_start → llm_start/llm_end(×N)
  → graph_end → speech_stream_done → speech_eou, with per-sentence-chunk
  speech_publish → tts_receive → tts_synth_start → tts_audio_start → tts_end
  interleaved (streaming TTS), then tts_eou_receive → tts_utterance_end.

End-to-end = injection (or stt_end) → FIRST tts_audio_start, compared to
--budget. With streaming this fires on the first sentence, mid-generation.
Cross-machine timestamps assume NTP-synced clocks; negative deltas are flagged.
"""

import argparse
import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

_DEFAULT_UTTERANCES = [
    "hello, how are you today?",
    "what can you do for me?",
    "tell me a fun fact about space",
]

# A turn is finished once one of these arrives (tts_utterance_end if TTS is
# running, speech_eou alone if it is not) and the pipeline then goes quiet.
_TERMINAL_STAGES = {"tts_utterance_end", "speech_eou", "tts_end", "speech_publish"}
_QUIET_GRACE_S = 3.0


class LatencyProbe(Node):
    def __init__(self):
        super().__init__("latency_probe")
        self._events: list[dict] = []
        self._pub = self.create_publisher(String, "/voice/user_input", 10)
        self.create_subscription(String, "/diag/timing", self._on_timing, 50)

    def _on_timing(self, msg: String):
        try:
            event = json.loads(msg.data)
        except ValueError:
            return
        event["_recv"] = time.time()
        self._events.append(event)

    def inject(self, text: str) -> float:
        t = time.time()
        self._pub.publish(String(data=text))
        return t

    def take_events(self) -> list[dict]:
        events, self._events = self._events, []
        return events

    def wait_for_turn(self, timeout: float) -> list[dict]:
        """Spin until a terminal stage arrives and the pipeline goes quiet."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            terminal = [e for e in self._events if e["stage"] in _TERMINAL_STAGES]
            if terminal:
                quiet_since = max(e["_recv"] for e in self._events)
                if time.time() - quiet_since > _QUIET_GRACE_S:
                    break
        return self.take_events()


def print_waterfall(events: list[dict], t0: float, label: str, budget: float):
    print(f"\n═══ {label} ═══")
    if not events:
        print("  (no timing events received — is agent_node running?)")
        return

    events = sorted(events, key=lambda e: e["t"])
    prev = t0
    skew = False
    for e in events:
        rel, step = e["t"] - t0, e["t"] - prev
        skew |= step < -0.05
        extra = "  ".join(
            f"{k}={v}" for k, v in e.items() if k not in ("stage", "t", "_recv")
        )
        print(f"  +{rel:7.3f}s  Δ{step:+7.3f}s  {e['stage']:<16} {extra}")
        prev = e["t"]

    # First occurrence of each stage — streamed turns emit per-chunk events and
    # the latency that matters is to the FIRST audio out.
    by_stage: dict = {}
    for e in events:
        by_stage.setdefault(e["stage"], e["t"])
    end = by_stage.get("tts_audio_start") or by_stage.get("speech_publish")
    if end is not None:
        e2e = end - t0
        mark = "✅ within" if e2e <= budget else "❌ over"
        anchor = ("first tts_audio_start" if "tts_audio_start" in by_stage
                  else "first speech_publish")
        print(f"  ── end-to-end: {e2e:.3f}s to {anchor} — {mark} {budget:.1f}s budget")
    if skew:
        print("  ⚠ negative step seen — check NTP sync between Pi5/Jetson")


def replay(probe: LatencyProbe, utterances: list[str], timeout: float,
           settle: float, budget: float):
    print(f"Replaying {len(utterances)} utterance(s) — budget {budget:.1f}s "
          f"(note: replay skips mic/STT; live STT cost shows only in --listen)")
    for text in utterances:
        probe.take_events()  # drop anything stale
        t0 = probe.inject(text)
        events = probe.wait_for_turn(timeout)
        print_waterfall(events, t0, f'"{text}"', budget)
        time.sleep(settle)


def listen(probe: LatencyProbe, budget: float):
    print("Listening for timing events (talk to the robot; Ctrl-C to stop)…")
    turn: list[dict] = []
    try:
        while True:
            rclpy.spin_once(probe, timeout_sec=0.2)
            for e in probe.take_events():
                # A new stt_vad_end / brain_receive starts a fresh turn.
                if e["stage"] in ("stt_vad_end", "brain_receive") and any(
                    x["stage"] in _TERMINAL_STAGES for x in turn
                ):
                    _flush_listen_turn(turn, budget)
                    turn = []
                turn.append(e)
            if turn and time.time() - max(x["_recv"] for x in turn) > _QUIET_GRACE_S \
                    and any(x["stage"] in _TERMINAL_STAGES for x in turn):
                _flush_listen_turn(turn, budget)
                turn = []
    except KeyboardInterrupt:
        if turn:
            _flush_listen_turn(turn, budget)


def _flush_listen_turn(turn: list[dict], budget: float):
    # Anchor live turns at STT completion; the user actually stopped speaking
    # ~silence_timeout before stt_vad_end (reported in that event).
    by_stage = {e["stage"]: e["t"] for e in turn}
    t0 = by_stage.get("stt_end") or by_stage.get("stt_vad_end") or turn[0]["t"]
    print_waterfall(turn, t0, "live turn (t0 = stt_end)", budget)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("utterances", nargs="*", default=_DEFAULT_UTTERANCES,
                    help="texts to inject (default: 3 canned utterances)")
    ap.add_argument("--listen", action="store_true",
                    help="passive mode: group live events, inject nothing")
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="max seconds to wait per turn (default 90)")
    ap.add_argument("--settle", type=float, default=2.0,
                    help="pause between injected turns (default 2)")
    ap.add_argument("--budget", type=float, default=2.0,
                    help="end-to-end latency budget in seconds (default 2.0)")
    args = ap.parse_args()

    rclpy.init()
    probe = LatencyProbe()
    try:
        if args.listen:
            listen(probe, args.budget)
        else:
            replay(probe, args.utterances, args.timeout, args.settle, args.budget)
    finally:
        probe.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
