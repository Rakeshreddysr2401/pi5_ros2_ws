# Low-level design — the Pi 5 brain

What each file is, why it exists, and what happens between "you speak" and
"the wheels turn". Read [HOW_IT_WORKS.md](HOW_IT_WORKS.md) first if you want
the narrative; this is the reference.

Scope: `pi5_ros2_ws` only. The rover's perception stack (cuVSLAM, nvblox,
Nav2) lives in the `-langrobo_perception-` repo; where the two meet is
[INTEGRATION_GAPS.md](INTEGRATION_GAPS.md).

---

## 1. The one rule that shapes everything

**`src/langrobo_core` never imports `rclpy`.**

```
src/langrobo_core/     pure Python. The brain. Runs and tests on any laptop.
src/langrobo_ros/      the ONLY ROS2 code. agent_node + ROS2Bridge + launch.
src/pi5_voice_pkg/     STT and TTS. Talks to the brain over /voice/* topics only.
```

`langrobo_core` reaches the robot through one object it never constructs:

```python
from ._bridge import get
get().publish_twist(twist)      # ROS2Bridge on the robot, StubBridge on a laptop
```

`agent_node` calls `_bridge.init(ROS2Bridge(...))` at startup; `graph_studio.py`
and the tests call `_bridge.init(StubBridge())`. That single seam is why 136
tests run with no robot, no LLM server and no API keys.

The cost of the rule: `StubBridge` must implement every public method
`ROS2Bridge` has, and no more. A missing one is not a test problem — it
surfaces as an `AttributeError` inside a tool, which the model then reports to
the user as a robot fault (`ground_pixel` was missing exactly that way). A
method the stub has and the real bridge lacks is the mirror image: a tool that
passes every test and breaks on the robot.

`tests/test_bridge_parity.py` checks both directions by parsing the two files'
ASTs — no import, so it runs with no ROS2 installed.

---

## 2. The three agents

One per modality, and no router above them.

| agent | owns | KV slot | sticky |
|---|---|---|---|
| `chat` | text in, text out. The default responder **and the router**. | 0 | yes |
| `local_agent` | images in. The only agent with `look()`. | 1 | yes |
| `navigate` | motion out. The only agent that moves wheels. | 2 | no |

There was a fourth — a `supervisor` that did nothing but route. It was removed
on 2026-09-07 because it had stopped doing that: agent_node enters every user
turn at `chat` or the sticky agent, so the supervisor only ever saw `[SYSTEM]`
turns, of which this build produces exactly one kind (navigation arrival),
which always routes to `chat`. A whole agent, prompt, node and KV slot to make
a decision with one possible answer.

Everything about an agent is one `AgentSpec` in
`langrobo_core/registry.py`. Adding one is two edits — a name in
`agent_ids.py`, a spec in `registry.py` — and the graph topology, the handover
grammar, the routing table `chat` renders, the sticky set, the rendered tool
block and the KV slot all follow. Two import-time assertions fail if the two
files disagree, or if two agents claim the same slot.

**Why `navigate` is not sticky.** Sticky means the next turn re-enters the
same agent directly, skipping the routing hop. That is safe for agents that
answer in plain text and can re-route a topic change themselves. `navigate`
ends its turn with a plain confirmation and no routing opinion, so the turn
after it starts at `chat`.

---

## 3. A turn, end to end

```
 mic ──▶ stt_node ──▶ /voice/user_input ──▶ agent_node queue ──▶ worker thread
                                                                      │
                                     ┌── movement fast path? ─────────┤
                                     │   (regex, ZERO LLM calls)      │
                                     ▼                                │
                              tool + spoken ack                       │
                                                                      │
                                     ┌── vision question? ────────────┤
                                     │   (frame attached here)        │
                                     ▼                                ▼
                              enter local_agent               turn_entry
                                     │                                │
                                     │                     sticky agent, else chat
                                     └────────────┬─────────────────  ┘
                                                  ▼
                                    agent ──▶ its ToolNode ──▶ handover?
                                                  │                 │
                                                  ▼                 ▼
                              /voice/robot_speech ◀── chunks    another agent
                                     │
                                tts_node ──▶ speaker
```

### 3.1 The fast path (`langrobo_core/fastpath.py`)

Exact spoken movement commands never reach an LLM. `"stop"`, `"forward 30"`,
`"turn left 90"`, `"go to the kitchen"` match strict regexes and call the same
tool the `navigate` agent would, in milliseconds instead of seconds.

Two properties make it safe:

- `match()` is a pure function of `(text, known_locations)` and returns `None`
  whenever it is not *certain*. Anything ambiguous falls through to the graph.
- It speaks its acknowledgement **before** the slow action, not after — so
  `"come here"` says "Coming to you." immediately, then spends its VLM
  round-trip.

### 3.1b The vision entry shortcut (`fastpath.is_vision_question`)

Not a tool lane — the only thing in the codebase that changes where the graph
*starts*.

`"what do you see?"` used to cost **three** LLM calls: `chat` decides to hand
over, `local_agent` decides to call `look()`, `local_agent` answers with the
image. Measured at ~60s end to end on the 12B.

All three exist to reach a conclusion the matcher reaches for free: a question
about the current view needs the camera frame and the multimodal agent. So
agent_node grabs the frame itself, staples it onto the turn exactly the way
`look()` would, and enters at `local_agent` — which answers in **one** call.

The image lands in the shared history, so `"did he wear spectacles?"` still
works as a follow-up, and `local_agent` is sticky so that follow-up also skips
routing. Two guards keep it honest:

- It fires only when a **fresh frame actually exists**. With the camera down
  the normal path is better, because `look()` reports the outage in words
  instead of the model guessing at an empty conversation.
- `look around`, `look left`, `scan the room` are movement and are explicitly
  excluded — a movement command must never be answered with a photo.
  `test_vision_and_movement_lanes_never_both_claim_an_utterance` enforces it.

### 3.2 Entry routing (`graph/turn_entry.py`)

| the turn | starts at | LLM calls to the answer |
|---|---|---|
| exact movement command | no graph at all | **0** |
| certain vision question | `local_agent`, frame attached | **1** |
| follow-up after `chat` (sticky) | `chat` | 1 |
| follow-up after `local_agent` (sticky) | `local_agent` | 1 |
| fresh general question | `chat` | 1 |
| fresh question for another agent | `chat` → handover | 2 |
| after `navigate` (not sticky) | `chat` | 1 |
| a `[SYSTEM]` event | `chat` | 1 |

`chat` is the default because it carries the routing table itself, so the
common case costs **one** LLM call rather than a serial router→agent pair.

### 3.3 Handover (`tools/handover.py`, `graph/handover_resolver.py`)

The only way control moves between agents. `next_agent` is a `Literal` built
from `agent_ids.ROUTABLE`, so llama.cpp compiles that enum into the decoding
grammar — a small model **physically cannot** emit a route to an agent that
does not exist.

`chain=False` means "I have answered, end the turn". `chain=True` means "the
next agent must act on my result" (local_agent identifies an object, navigate
drives to it). The reason string is the only information that crosses: other
agents cannot see images, so `local_agent`'s reason text is all `navigate`
gets.

### 3.4 Loop guard (`graph/build.py`)

Each agent node may execute at most 8 times per turn. Past the cap it
short-circuits with a plain reply instead of calling the LLM again. This
catches the degenerate case an agent re-calling its own tools forever — a path
that never reaches `handle_handover`, so the handover visit counter cannot see
it.

---

## 4. Latency: where the seconds go, and what buys them back

On the 12B model over the Mac Mini's llama.cpp, prompt *prefill* dominates. The
whole design below is about not paying for it twice.

### 4.1 One KV slot per agent — the parallel cache

Each agent's system prompt is a different ~1-2k token prefix. A server started
with `--parallel N` keeps N independent KV caches. Pin each agent to its own
(`id_slot`) and its prefix stays resident, so a turn prefills only the new
tokens.

Share a slot between two agents and **each call evicts the other's prefix** —
measured at 18-50s of re-prefill per turn.

```
registry.py            AgentSpec.slot          ← declared beside the agent
      │
      ▼
registry.SLOTS         {chat: 0, local_agent: 1, navigate: 2}
      │
      ▼
agent_node.__init__    probes GET /props for the server's real slot count,
                       wraps with modulo if it is smaller, and WARNS
      │
      ▼
services/llm.configure(agent_overrides={name: {"slot": n}})
      │
      ▼
ChatOpenAI(extra_body={"id_slot": n})     ← forwarded verbatim to llama.cpp
```

**Start the server with `--parallel 3`.** Fewer slots is not an error; it just
costs, and agent_node says so at boot.

This replaced five hand-maintained ROS parameters plus a fold-when-out-of-range
algorithm. The slot is a property of the agent, so it now lives next to the
agent.

### 4.2 KV-cache discipline (the rules that keep a prefix stable)

A prefix is only cached while it is byte-identical. Four rules protect that:

1. **No clock in a system prompt.** Only the date. The time is the
   `get_current_time` tool — a per-minute timestamp re-prefilled ~2k tokens on
   every minute tick.
2. **Dynamic text goes at the END.** `AgentSpec.context` is appended after the
   static prompt, never spliced into it.
3. **Message projection is append-only** (`utils/message_utils.py`). Never
   drop or reorder mid-history messages.
4. **History trims only at `HumanMessage` boundaries** (`utils/history.py`).

### 4.3 The cache warmer

After every turn, `agent_node._start_cache_warm()` sends the *identical* prompt
the next turn will send, with `max_tokens=1`, on a background thread. It shares
`build_llm_call` with the real path — that is what makes "identical"
guaranteed rather than aspirational. It skips itself if input is already
pending.

### 4.4 Sentence streaming to TTS

`utils/speech_stream.py` hangs a callback off the LLM run. Complete sentences
are published to `/voice/robot_speech` **as tokens arrive**, so synthesis
starts on sentence 1 while the model is still writing sentence 3. The utterance
ends with a `<|eou|>` marker, which is what releases the mic.

Sentence boundaries are punctuation *or* a bare newline, because the output is
spoken: a markdown list is read aloud bullet characters and all. That is why
`SPEECH_STYLE` bans markup outright rather than discouraging it.

---

## 5. File map

### `langrobo_core/` — the brain (pure Python)

| file | what it is |
|---|---|
| `agent_ids.py` | the agent names. Zero imports, so `tools/handover.py` can use it without a cycle. |
| `registry.py` | **one `AgentSpec` per agent.** Prompt, tools, KV slot, sticky, keep_images. The file to read first. |
| `prompts.py` | every system prompt, in one file. Read top to bottom to see everything the robot is told to be. |
| `fastpath.py` | the deterministic movement lane — regex to wheels, no LLM. |
| `agents/factory.py` | builds a node from a spec. **Every** agent is this function — there are no hand-written nodes. |
| `graph/build.py` | the StateGraph. Derived entirely from `registry.SPECS`; adding an agent needs no edit here. |
| `graph/turn_entry.py` | which agent a turn enters: the sticky one, or chat. |
| `graph/handover_resolver.py` | executes a handover; guards against loops. |
| `graph/state.py` | `AgentState` — messages, active agent, per-turn counters, sender identity. |
| `tools/` | `@tool` functions. Per-agent sets in `__init__.py`. Robot I/O via `_bridge.get()`. |
| `services/` | state that outlives a turn: `config`, `llm`, `telegram`, `permissions`, `health`, `logging`, `metrics`. |
| `utils/` | pure helpers: history trimming, message projection, sentence streaming, timing. |
| `bridges/stub.py` | the no-ROS bridge. Must mirror `ROS2Bridge`'s public surface. |

### `langrobo_ros/` — the ROS2 shim

| file | what it is |
|---|---|
| `agent_node.py` | the entry point: params → bridge → services → graph → worker thread. |
| `ros2_bridge.py` | every topic, service and action. `NAV_FRAME` is defined here, once. |
| `wheel_odom_relay.py` | ESP32 `/wheel_state` → `nav_msgs/Odometry`. |

There is deliberately no custom-interface package. Every topic here is a
`std_msgs`/`geometry_msgs`/`sensor_msgs` type or JSON in a `String`, which is
what lets the Jetson side (a frozen container that cannot be rebuilt) speak
the same contract.

### `pi5_voice_pkg/` — voice

| file | what it is |
|---|---|
| `stt_node.py` | mic → VAD → wake gate → provider → `/voice/user_input`. |
| `tts_node.py` | `/voice/robot_speech` → provider → speaker, with barge-in. |
| `vad_gate.py` | the noise gate between VAD and the recogniser. Pure, tunable. |
| `wake/`, `stt_providers/`, `tts_providers/` | swappable back ends; a provider failure falls back to local. |

---

## 6. Threading

`agent_node` runs `rclpy.spin()` — a **SingleThreadedExecutor**. Know which
thread you are on:

| thread | runs | must not |
|---|---|---|
| ROS spin | every subscription callback, the image cache | block. Ever. |
| worker | the whole graph, every tool, every LLM call | — |
| nav worker | one Nav2 action, per goal | touch graph state |
| telegram poller | long-poll `getUpdates` → worker queue | reply directly |
| cache warmer | one prefill request | run when input is pending |

Blocking I/O started from the spin thread always gets its own short-lived
thread. A tool that blocks is fine — it is on the worker.

The queue between them has three lanes: user input (newest wins — a stale
question should not be answered), system events (FIFO, never dropped), and
Telegram (FIFO, drained after voice, because the person in the room comes
first).

---

## 7. Failure behaviour

**Missing keys degrade, never crash.** No `TAVILY_API_KEY` → `WEB_TOOLS` is
empty and chat says it cannot look that up. No Telegram allowlist → the
channel stays off. Mac Mini down → the cloud fallback, or a spoken offline
message.

**Honesty over confidence** is enforced in the tools, not the prompts:

- `get_frame(max_age_s=10)` returns `None` rather than a stale scene, so
  `look()` and `send_telegram_photo` admit blindness.
- `get_current_pose()` returns `None` when TF has no fix, so movement tools
  say they cannot localise rather than driving on a guess.
- `point_camera` is not bound at all without servos (`LANGROBO_PAN_TILT`), and
  reports that no mount is fitted if you call it anyway. An always-refusing
  tool still costs prompt tokens every turn and still tempts the model.
- `ground_pixel`'s timeout means *the query never arrived*, which the tool
  reports differently from *grounding failed*.

**Permissions are enforced in tools** (`services/permissions.py`), never only
in prompts. A prompt can be talked around; `if cap not in role` cannot.

---

## 8. Extending it

**Add an agent:** a name in `agent_ids.py`, an `AgentSpec` in `registry.py`
with the next free `slot`, a prompt in `prompts.py`. Raise `--parallel`.
Nothing else — `test_smoke.py` fails if you miss a piece.

**Add a tool:** an `@tool` function in `tools/`, added to one set in
`tools/__init__.py`. It appears in that agent's rendered prompt automatically.
Keep the sets short: every tool costs prompt tokens on every turn, forever.

**Add a proactive behaviour:** a producer that injects a `[SYSTEM]` turn via
`bridge.enqueue_system_turn()`. Do not invent a second mechanism — the nav
arrival report is the worked example.

**Restore something that was removed:** it is in git, on
`dev-1.2.8-refactor-test`. The Swiggy/Instamart/Dineout/tracker agents, the
knowledge agent and its document ingest, the briefing agent, reminders,
household lists, music, home-watch, and the Qdrant episodic memory all lived
there. `INTEGRATION_GAPS.md` §1 carries the `/vision/detections_3d` contract to
restore `approach_object` and the world model against.
