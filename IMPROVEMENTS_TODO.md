# Improvements — Status, Blockers & TODO

Tracking doc for the agent-brain modernization work. Updated 2026-06-28.

> Context: target is **fully-local ASAP** (Gemma via llama.cpp on the Mac Mini),
> with OpenAI as an *optional* fallback behind the provider switch. The dev venv on
> the Mac is the LangGraph Studio env; the production brain runs under ROS2 on the Pi5.

---

## 1. Done this session (validated where possible)

| Area | Change | Validation |
|------|--------|-----------|
| **Framework** | Upgraded LangGraph `0.2/0.3 → 1.2.6` + langchain-core `1.4.8` + integration pkgs to 1.x. Re-pinned `requirements.txt`, dropped unused `langchain-community`. | ✅ Real graph builds + compiles with a checkpointer in the upgraded venv |
| **Routing** | Forced supervisor `handover` via `tool_choice` (grammar-constrained on llama.cpp). Flag `strict_tool_calls`. | ⚠️ needs `--jinja` on Pi5; hardware test |
| **Routing** | Sticky routing for `chat`/`local_agent` (skip supervisor hop on follow-ups). | ⚠️ hardware test |
| **Cleanup** | Removed dead `vision` node everywhere (incl. handover enum). | ✅ grep clean, builds |
| **Prompt** | `chat` no longer advertises a `tavily_search` tool it doesn't have. | ✅ |
| **Orders** | Structural **order-confirmation gate** (`graph/nodes/swiggy_tools.py`): an order can't be placed in a single shot — needs a re-call on a *later* user turn (bridge turn counter). No checkpointer, no new deps. | ✅ pure-logic unit test (`test/test_order_gate.py`); ⚠️ end-to-end blocked (see below) |

Earlier, already-committed batch: STT VAD-framing fix + `min_speech` (speech_vision repo),
input-queue data-loss fix, motion interruptibility ("stop" reaches wheels), vision compressed-topic.

---

## 2. BLOCKERS (features that are coded but NOT yet trustworthy)

### B1 — Swiggy MCP is non-functional in dev (0 tools)
- `SWIGGY_FOOD_TOOLS` loads **0 tools** (no `SWIGGY_ACCESS_TOKEN`; connection to
  `mcp.swiggy.com/food` fails). So the whole ordering flow can't be exercised.
- **Consequence:** the order-confirmation gate is logic-tested but **never run against a
  real order tool**. The order-tool name patterns in `swiggy_tools.py::_ORDER_PATTERNS`
  are a guess (`place_order`, `checkout`, `pay`, …) — **verify against the real tool names**
  once the MCP works, or the gate silently won't fire.
- **TODO:** obtain a Swiggy token → set `SWIGGY_ACCESS_TOKEN` in `.env` → enumerate tool
  names → confirm/adjust `_ORDER_PATTERNS` → run a real 2-turn order test.

### B2 — langgraph 1.x must be installed + tested on the Pi5
- The graph zone is validated only in the **Mac venv**. `agent_node.py` (ROS2) is not
  importable here. It uses the same `graph.stream(...)`/`HumanMessage` APIs the build
  exercises, so confidence is high but unproven on hardware.
- **TODO:** Pi5 upgrade procedure in §4, then the smoke tests in §3.

### B3 — Grammar-constrained tool calls need `--jinja`
- `strict_tool_calls=true` forces the supervisor `handover` via `tool_choice`. On
  llama.cpp this only works if the server is launched with **`--jinja`** (tool-aware
  template). Without it, the request may error/ignore the constraint.
- **TODO:** confirm the Mac Mini llama.cpp server is started with `--jinja`; else set
  `strict_tool_calls: false` in `agent_params.yaml`.

---

## 3. Hardware smoke-test checklist (run on the robot after Pi5 upgrade)

- [ ] **STT short command:** say "stop" — confirm it transcribes (was dropped before).
- [ ] **Motion interrupt:** during "go to the cup", say "stop" — wheels halt promptly.
- [ ] **Sticky vision:** "what do you see?" then "what colour is it?" — logs show
      `entry=local_agent` (no supervisor) on turn 2; reuses the cached frame.
- [ ] **Topic change while sticky:** during a vision chat say "order pizza" — reaches swiggy.
- [ ] **System event while sticky:** with an active order, let the 2-min delivery poll fire —
      routes to `tracker`, not the sticky agent.
- [ ] **Grammar routing:** a few varied requests — supervisor emits exactly one clean
      `handover` (check logs); no stray text / invalid agent.
- [ ] **Vision bandwidth:** confirm `look()` returns a valid image off
      `/camera/color/image_raw/compressed`.
- [ ] **Order gate (after B1):** "order biryani" → robot reads back summary + asks; it does
      NOT place on the first call; only after you say "yes" does it place.

---

## 4. Pi5 upgrade procedure + rollback

```bash
# On the Pi5 ROS2 Python env:
pip freeze > ~/pre_lg1x_freeze.txt          # snapshot for rollback
pip install -r requirements.txt              # pulls langgraph 1.2.6 etc.
colcon build --packages-select ai_agent --symlink-install
```
**Rollback:** `git checkout HEAD~1 requirements.txt && pip install -r requirements.txt`
(pre-upgrade pin was `langgraph==0.3.34`), or `pip install -r ~/pre_lg1x_freeze.txt`.

> You preferred to avoid Pi5 pip installs — this langgraph upgrade is the *one* you
> approved. No other new deps were added (no sqlite-saver, no swarm/supervisor libs).

---

## 5. Backlog / future improvements (ranked)

### Local-model accuracy (biggest lever for the fully-local goal)
1. **Swap the Mac Mini model** from Gemma 3n E4B to a stronger tool-caller. On 16 GB:
   sweet spot is a 7–8B (e.g. Qwen2.5-7B-Instruct) at Q4_K_M/Q5; a 12B @ Q4 is the edge.
   Verify multimodal (for `look()`) or keep a small multimodal model via `local_agent_model`.
2. **Cut `n_ctx` 131072 → ~8192** on the llama.cpp server — frees the RAM making local tight.
3. **GBNF grammar** already leveraged via `tool_choice`; extend forced tool_choice to
   swiggy/tracker mandatory steps once `--jinja` is confirmed.

### Voice
4. **Silero VAD** to replace hand-rolled WebRTC endpointing (better than the framing fix).
5. Keep the **sandwich** STT→LLM→TTS architecture (this is the LangChain-recommended one;
   speech-to-speech / realtime is cloud-locked — not for the local goal).

### Agent framework hygiene
6. ✅ **DONE — `swiggy_mcp.py` no longer hits the network at import.** Replaced
   `SWIGGY_FOOD_TOOLS = _load_sync()` with lazy memoized `get_food_tools()`; tool sets
   are now `get_swiggy_tools()` / `get_tracker_tools()` (load once at graph build, never
   at import). Set `SWIGGY_ENABLED=0` to skip the load entirely (no network).
7. **Order gate → full LangGraph `interrupt()`** once a checkpointer is adopted: makes the
   confirmation a true framework pause/resume (current gate is structural via turn-advance,
   which is good, but `interrupt()` is the canonical HITL). Deferred: checkpointer adoption
   was judged *not* worth it for the single-conversation in-memory loop (would add
   `RemoveMessage` trimming complexity to match the KV-cache-friendly history trimming).
8. **Deliberately NOT adopted** (would add risk, not reduce it, for this design):
   `create_react_agent` (fights per-agent image projection + centralized handover),
   checkpointer-for-history, `langgraph-swarm`/`langgraph-supervisor` libs (your custom
   routing is more integrated with ROS2). Revisit only if the custom code becomes a burden.

### Vision
9. Frame **staleness check** in `look()` (timestamp the cached frame; force a fresh capture
   if older than ~N seconds) — currently prompt-guided only.
