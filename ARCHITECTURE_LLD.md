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
and the tests call `_bridge.init(StubBridge())`. That single seam is why 182
tests run with no robot, no LLM server and no API keys.

The cost of the rule: `StubBridge` must implement every public method
`ROS2Bridge` has, and no more. A missing one is not a test problem — it
surfaces as an `AttributeError` inside a tool, which the model then reports to
the user as a robot fault (`ground_pixel` was missing exactly that way). A
method the stub has and the real bridge lacks is the mirror image: a tool that
passes every test and breaks on the robot.

**`ROS2Bridge` subscribes the camera itself**, and the same goes for any ROS
input feeding a cache this class owns. The frame subscription used to live in
`agent_node`, so only *that* owner of a bridge ever filled the cache:
`graph_studio.py` had a fully wired `ROS2Bridge` whose `get_frame()` returned
`None` forever, `look()` answered "no camera frame is available" and
`approach_described_object` could never see — on a robot whose camera was
publishing the whole time. Fixed 2026-09-09. Wiring that lives in two places
drifts, and the half nobody watches goes quiet — the same failure family as
`NAV_FRAME`. `use_vision` stays a real switch: it is a constructor argument fed
from the ROS param, so turning vision off still drops the Jetson↔Pi5 image
traffic.

`tests/test_bridge_parity.py` checks both directions by parsing the two files'
ASTs — no import, so it runs with no ROS2 installed.

---

## 2. The three agents

One per modality, and no router above them.

| agent | owns | KV slot | sticky |
|---|---|---|---|
| `chat` | text in, text out. The default responder **and the router**. | 0 | yes |
| `local_agent` | images in. The only agent with `look()`. | 1 | yes |
| `navigate` | motion out. The only agent that moves wheels. | 2 | yes |

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
block and the KV slot all follow. Three import-time assertions fail the moment
they drift: the registry and `agent_ids` must name the same agents, every
routable target must have a spec, and no two agents may claim the same KV slot.

All three are built by the same twenty-line factory (`agents/factory.py`).
There are no hand-written agent nodes — the one that existed was the router.

**All three are sticky.** Sticky means the next turn re-enters the same agent
directly, skipping the routing hop — worth one whole LLM round-trip per
follow-up turn. The requirement is that the agent can re-route a topic change
itself, because a sticky agent is handed follow-ups that may not be its own.

`navigate` was NOT sticky until 2026-09-08, on the grounds that it ends its
turn with a plain confirmation and no routing opinion. Removing the regex fast
path (§3.1) changed the arithmetic: every movement command now costs
`chat` + handover + `navigate`, and a multi-step drive ("forward a metre" …
"now turn left") paid that on every step. Sticky makes each follow-up one call.

The trade is real and is paid in `NAVIGATE_PROMPT` rule 6: a non-movement
follow-up ("what's the weather") now lands on `navigate` first and must be
handed back to `chat`, costing 2 calls where it used to cost 1. Movement
follow-ups are the common case after a move, so this is the right side of the
trade — but the rule is what makes it safe, so registry and prompt must stay
in step.

---

## 3. A turn, end to end

```
 mic ─▶ stt_node ─▶ /voice/user_input ─▶ agent_node queue ─▶ worker thread
                                                                    │
                                                                    ▼
                                                              turn_entry
                                                       sticky agent, else chat
                                                                    │
                                                                    ▼
                                                                  agent
                                                                    │
                                                                    ▼
                                                              its ToolNode
                                                                    │
                                                 handover? ─────────┴──── no tool calls left
                                                     │                        │
                                                     ▼                        ▼
                                               another agent                 END
                                                                              │
        speaker ◀─ tts_node ◀─ /voice/robot_speech ◀──────────────── sentence chunks
                                   (streamed while the LLM is still writing)
```

### 3.1 The fast path — REMOVED (was `langrobo_core/fastpath.py`)

Until 2026-09-08 two regex lanes sat in front of the graph. `match()` mapped
exact spoken movement commands (`"stop"`, `"forward 30"`, `"go to the
kitchen"`) straight onto the tool the `navigate` agent would have called, at
zero LLM calls; `is_vision_question()` recognised `"what do you see?"` and
entered `local_agent` with the camera frame already attached, at one call
instead of three.

**Both are gone.** They were fast and they never guessed — `match()` returned
`None` whenever it was not certain — but the intent vocabulary was a hand-kept
lookup table (48 COCO class strings, an alias map, a spelled-out-number map,
eleven regexes) and it had already drifted from the robot underneath it:

- `_canon_object()` gated every object on `COCO_CLASSES`, but the only tool it
  dispatched to is `approach_described_object`, which is a **VLM** path. The
  YOLO tool that needed a COCO vocabulary was deleted when we found nothing
  publishes `/vision/detections_3d` (§1 of INTEGRATION_GAPS.md). So
  `"go to the red bottle"` failed the gate and fell through to the LLM for no
  reason other than a stale word list.
- `_execute()`'s `look` intent called `point_camera` directly, bypassing the
  `PAN_TILT_ENABLED` gate that keeps that tool out of every agent's tool set
  on a robot with no servo mount.

The replacement is the model itself: `chat` reads the utterance and hands over.
That is slower — see the table in §3.3 — and a MiniLM entry classifier is
planned to win the latency back without a lookup table. The design is written
up in **INTENT_ROUTING_PLAN.md**; it is not built.

What did *not* depend on the fast path, and still holds:

**Stopping is not an LLM decision.** `agent_node._on_user_input` calls
`cancel_navigation()` + `request_motion_stop()` on every incoming utterance
before the graph runs, and `movement._drive_for_duration` polls that flag every
5 ms and publishes a zero Twist on abort. The wheels halt in milliseconds
whatever the model later concludes. The removed `"stop"` lane spoke the
*acknowledgement* early; it was never the brake.

### 3.3 Entry routing (`graph/turn_entry.py`)

| the turn | starts at | LLM calls to the answer |
|---|---|---|
| follow-up after `chat` (sticky) | `chat` | 1 |
| follow-up after `local_agent` (sticky) | `local_agent` | 1 |
| fresh general question | `chat` | 1 |
| a `[SYSTEM]` event | `chat` | 1 |
| movement command | `chat` → handover → `navigate` | **2** |
| vision question | `chat` → handover → `local_agent` → `look()` → answer | **3** |
| movement follow-up after `navigate` (sticky) | `navigate` | 1 |
| non-movement follow-up after `navigate` | `navigate` → handover → `chat` | 2 |

The last three rows are the cost of removing the fast path (§3.1). The
three-call vision path was measured at ~60 s end to end on the 12B. Recovering
them is what INTENT_ROUTING_PLAN.md is for.

`chat` is the default because it carries the routing table itself, so the
common case costs **one** LLM call rather than a serial router→agent pair.

### 3.4 Handover (`tools/handover.py`, `graph/handover_resolver.py`)

The only way control moves between agents. `next_agent` is a `Literal` built
from `agent_ids.ROUTABLE`, so llama.cpp compiles that enum into the decoding
grammar — a small model **physically cannot** emit a route to an agent that
does not exist.

The resolver's actual rule is two lines:

```
agent was silent (no AI text) OR chain=True  →  Command(goto=next) [immediate]
agent spoke AND chain=False                  →  dict update + END  [sticky]
```

So `chain=True` means "the next agent must act on my result" — local_agent
identifies an object, navigate drives to it. `chain=False` after the agent has
already spoken ends the turn. And an agent that hands over **without saying
anything** always chains, whatever it passed: a silent turn would otherwise
leave the user with no reply at all.

The reason string is the only information that crosses. Other agents cannot
see images, so `local_agent`'s reason text is all `navigate` ever gets about
what it saw.

### 3.5 Loop guard (`graph/build.py`)

Each agent node may execute at most 8 times per turn. Past the cap it
short-circuits with a plain reply instead of calling the LLM again. This
catches the degenerate case an agent re-calling its own tools forever — a path
that never reaches `handle_handover`, so the handover visit counter cannot see
it.

### 3.6 Vision-question backstop (`graph/build.py`, added 2026-09-10)

CHAT_PROMPT and NAVIGATE_PROMPT both say, as a numbered rule, to hand a
question about what the robot sees to `local_agent` — the only agent with
`look()`. Both are *instructions*. Two real transcripts the same evening
showed the 12B model ignoring them: once by just answering in plain text with
`navigate` sticky ("I am looking at the area around the chair", no `look()`,
no image anywhere in the turn), and once — because the first backstop only
checked "spoke with no tool call" — by proposing `scan_surroundings()`, a real
360° rotation nobody asked for, instead of a handover.

The fix lives in the SAME loop-guard wrapper as §3.5, not in either prompt: on
every agent step, before the graph's normal routing runs, check whether the
step is (a) not `local_agent`, (b) NOT a `handover` tool call, (c) `local_agent`
has not already answered this turn, and (d) the user's own last message
matches a narrow vision-question pattern (`_VISION_QUESTION`, matched against
the user's fixed words, never the model's free-form reply). On a hit, the
proposed reply or tool call is discarded — never executed, never added to
history — and a `Command(goto="local_agent")` carries a routing note instead,
chaining in the same turn. `agent_node` only speaks the final message once the
graph reaches `END`, so nothing wrong is ever said or driven.

Deliberately keyed on `handover` being present, not on `tool_calls` being
empty: the first version's "any tool call is fine" exemption is what let the
`scan_surroundings()` case through. Tests: `tests/test_vision_backstop.py`.

---

## 4. Latency: where the seconds go, and what buys them back

On the 12B model over the Mac Mini's llama.cpp, prompt *prefill* dominates.
There are two ways to win: don't make the call at all (§3.1 and §3.2 — a
movement command costs zero LLM calls, a vision question costs one instead of
three), or make sure the call you do make starts from a warm cache. This
section is the second half.

### 4.1 One KV slot per agent — the parallel cache

Each agent's system prompt is a different prefix — measured at ~941 (chat),
~881 (local_agent) and ~952 (navigate) tokens, plus the shared history. A
server started with `--parallel N` keeps N independent KV caches. Pin each
agent to its own (`id_slot`) and its prefix stays resident, so a turn prefills
only the new tokens.

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
5. **Per-turn state (a pose, a distance) goes at the TAIL of the message
   list, never into the system prompt** (`utils/pose_stamp.py`, added
   2026-09-10). The system prompt is the cached prefix — per-turn text there
   re-prefills the whole conversation, every turn, on every agent. `look()`
   stamps its frame with the pose it was taken FROM; `agent_node` stamps each
   user turn with where the robot is NOW; both ride on messages that were
   being appended anyway.

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
| `agents/factory.py` | builds a node from a spec. **Every** agent is this function — there are no hand-written nodes. |
| `graph/build.py` | the StateGraph. Derived entirely from `registry.SPECS`; adding an agent needs no edit here. Also the loop guard (§3.5) and the vision-question backstop (§3.6). |
| `graph/turn_entry.py` | which agent a turn enters: the sticky one, or chat. |
| `graph/handover_resolver.py` | executes a handover; guards against loops. |
| `graph/state.py` | `AgentState` — messages, active agent, per-turn counters, sender identity. |
| `tools/` | `@tool` functions. Per-agent sets in `__init__.py`. Robot I/O via `_bridge.get()`. |
| `tools/_bridge.py` | the seam itself — a module-level singleton, set once at startup. Twenty lines, and the reason the whole brain runs off-robot. |
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

The vision entry adds no thread: it reads the frame cache (a lock and a
`bytes` reference) on the worker, then invokes the graph as usual.

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
with the next free `slot`, a prompt in `prompts.py`, and a line in chat's
prompt telling it when to route there. Raise `--parallel` to match. Nothing
else — `test_smoke.py` and `test_prompt_contract.py` fail if you miss a piece,
including the chat-prompt line.

**If you add more than a couple of agents, put the router back.** `chat`
carrying the routing table is right at three agents and wrong at eight: its
prompt grows with every one, and it pays that growth on every single turn
including the ones that need no routing at all. The supervisor pattern exists
for exactly that trade — it is in git (see below).

**Add a tool:** an `@tool` function in `tools/`, added to one set in
`tools/__init__.py`. It appears in that agent's rendered prompt automatically.
Keep the sets short: every tool costs prompt tokens on every turn, forever.

**Add a proactive behaviour:** a producer that injects a `[SYSTEM]` turn via
`bridge.enqueue_system_turn()`. Do not invent a second mechanism — the nav
arrival report is the worked example.

**Restore something that was removed.** Nothing was deleted that is not in
git:

| what | where |
|---|---|
| the `supervisor` agent — prompt, node, forced `tool_choice`, `strict_tool_calls` | `git show bb8cc03^:src/langrobo_core/langrobo_core/agents/supervisor.py` |
| Swiggy / Instamart / Dineout / tracker agents and the MCP provider registry | branch `dev-1.2.8-refactor-test` |
| the knowledge agent + Telegram document ingest | `dev-1.2.8-refactor-test` |
| the briefing agent, reminders, household lists, music, home-watch | `dev-1.2.8-refactor-test` |
| Qdrant episodic memory + nightly consolidation | `dev-1.2.8-refactor-test` |
| `approach_object` and the persistent world model | `dev-1.2.8-refactor-test` |
| `studio_voice_node` (dev-mode voice against `langgraph dev`) | `dev-1.2.8-refactor-test` |

`INTEGRATION_GAPS.md` §1 carries the `/vision/detections_3d` JSON contract to
restore `approach_object` and the world model against — that topic having no
publisher is why they went, not anything wrong with the code.
