# 2026-07-03 — Random 20s replies: llama.cpp KV-cache cold prefill

Session report: what was checked, what was found, what was fixed, what remains.

## Context

Session goal: bring the stack up, verify everything works, fix what doesn't
(the STATUS.md P0 verify session, run off-robot from the Pi5).

Health checks that passed before the real work:
- Git clean and up to date with `origin/dev-1.0.6_with_fable`.
- `colcon build` clean for all 3 packages (src had drifted newer than install/).
- Jetson reachable (192.168.2.20, sub-ms ping); after brain start, all four
  Jetson nodes (`stt_node`, `tts_node`, `camera_node`, `target_node`) visible
  over DDS alongside `agent_node`.
- Mac Mini llama-server up, serving Gemma 4 12B (`/v1/models`).
- LangSmith tracing active, no 403 spam. Tavily enabled.
- Known/unchanged: Swiggy MCP 401 (stale `SWIGGY_ACCESS_TOKEN` in `.env`).

## Symptom

`scripts/latency_replay.py` showed wildly bimodal turns: some ~2–4s to first
audio, others **18–20s**. `llm_end dur_s` told the story: warm turns were pure
decode (~9.5 tok/s), slow turns re-prefilled the entire ~2,100-token chat
prompt (~105 tok/s prefill on the 12B ⇒ ~20s).

## Diagnosis method (reusable)

1. `curl http://singireddys-mac-mini.local:8080/props` → server runs
   **4 parallel slots**; `/slots` exposes `n_prompt_tokens_processed` per
   request — `processed ≈ prompt size` means total cache miss.
2. Synthetic A/B against a spare slot (`id_slot: 3`): identical prompt →
   proc=1; tail-only change → proc=18–26; with tools → proc≈500 (tools render
   AFTER the system text in this fork's Gemma-4 template). So the server's
   prefix reuse itself works.
3. Captured the brain's real request bodies with a ~40-line logging proxy
   (`brain_launch.py base_url:=http://127.0.0.1:9090/v1`), diffed consecutive
   turns: only the `== NOW ==` minute-resolution timestamp (mid-prompt, before
   tools) and the appended history differed — yet the server counters showed
   **zero reuse** on every minute tick, full reuse within the same minute.
4. Replaying the captured bodies on a different slot got near-full hits
   (proc=16–18), confirming request content was cache-friendly; the misses
   were about slot state + the mid-prompt divergence behavior of this build.

## Root causes & fixes (both in `ai_agent`, deployed to the Pi5 this session)

1. **Slot scatter** — sequential brain requests weren't pinned, so they could
   land on any of the 4 slots, each a cold cache; an unpinned vision turn
   could also evict the text agents' slot.
   Fix: global `llm_slot` param (default **0**) → every text-agent request
   carries `id_slot: 0` (`graph/llm.py` `configure(slot=…)`, `agent_node.py`);
   `local_agent_slot` default changed **-1 → 1** so vision keeps its own slot.
2. **Per-minute clock in the prompt** — chat's `== NOW ==` line diverged the
   prompt every wall-clock minute; on this server build that reliably produced
   a full re-prefill.
   Fix: prompt now carries **date only** (`== TODAY ==`, changes once/day);
   exact clock moved to a new **`get_current_time`** chat tool
   (`graph/tools/system.py`, registered in `CHAT_TOOLS`). "What time is it?"
   now costs one extra warm roundtrip (~2s) instead of taxing every turn.

## Verification (replay, spanning forced minute ticks)

| Turn | Before | After |
|------|--------|-------|
| First after restart | ~18–20s (cold — unavoidable once) | same, expected |
| Same-minute follow-up | ~2–4s | ~2–4s |
| **Across a minute tick** | **20.6s (full re-prefill)** | **4.2s (39-tok reply, zero re-prefill)** |
| "what time is it?" | in-prompt (but see above) | 1.9s tool call + 3.0s answer, correct |

## Remaining gaps (not caching)

- **First-audio is decode-bound now**: ~9.5 tok/s on the 12B ⇒ ~4–7s to first
  sentence + Kokoro synth. Meeting the ≤2s budget realistically means the
  3n E4B model the config originally assumed. Owner decision.
- **Pi5↔Jetson clock skew ~1.2–1.8s** (replay flags negative deltas). Pi5 NTP
  is fine; the Jetson needs chrony peering — no SSH access from the Pi5.
- **Unattributed ~42-token requests** occasionally land on slot 0 (seen in
  `/slots` task ids between brain turns). Harmless now (next turn is
  append-only and recovers from the server's prompt cache), but if cold turns
  reappear, identify that client first.
- Swiggy 401 — refresh `SWIGGY_ACCESS_TOKEN`.
- Acoustic verification (wake word, stop spotter, mic mute across chunks)
  still needs a human talking to the robot — DEPLOY.md checklists.
