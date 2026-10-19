# llama-server on the Mac Mini: prompts under ~1024 tokens are never cached

**For:** the agents working on the Mac Mini (`singireddys-mac-mini.local`).
**From:** the robot brain on the Pi 5 (repo `~/ros2_ws`), measured 2026-10-11.
**The full task list for the Mac is `MAC_MINI_TASKS.md` (repo root); this
file is the evidence for its Task 3.**
**Ask:** fix the server so short prompts reuse their slot's cache, restart it with
5 slots, and run the check at the bottom. Report back the server log lines and
the check output.

---

## 1. What is wrong

The robot sends every request with `id_slot` (one fixed slot per caller) and
`cache_prompt: true`, and relies on llama.cpp reusing the slot's cached prefix.

On the current server, **a slot's cache is reused only when the prompt is longer
than ~1024 tokens.** Below that it is thrown away every time, even when the
exact same prompt is sent twice in a row. It doesn't matter whether the prompt
is text or images.

Measured from the Pi 5, slot 3, each test = reset the slot, send prompt 1, then
send prompt 2 that shares all of prompt 1 except the last ~5 tokens (a new
question):

| prompt size (tokens) | 2nd request recomputed | reused | time |
|---|---|---|---|
| 234   | 234  | 1    | 2.0 s |
| 714   | 714  | 1    | 6.4 s |
| 894   | 894  | 1    | 8.4 s |
| 1014  | 1014 | 1    | 9.6 s |
| **1134**  | 11   | **1124** | **1.2 s** |
| 1324  | 11   | 1314 | 1.3 s |
| 1991  | 11   | 1981 | 0.7 s |
| 3251  | 11   | 3241 | 0.7 s |

So the cut-off is between 1014 and 1134 tokens, which is almost certainly
**Gemma's sliding-window size (1024)**.

The same thing with photos (4 camera photos ≈ 887 tokens):

| test | reused | time |
|---|---|---|
| 4 photos, new question (rewind ~5 tokens) | 1 | 9.0 s |
| 4 photos, **identical** request again | 1 | 9.0 s |
| 4 photos, append a follow-up turn | 1 | 11.8 s |
| ~400 tokens of text + the same 4 photos (1221 tokens), new question | **1211** | **0.8 s** |
| 3k text + 4 photos, new question | 4108 | 0.9 s |

The photos themselves cache fine. Only the total length matters.

Text caching for long prompts is perfect, including across slots: one slot keeps
its cache while another slot is used (3.2k-token prompts: 0.7 s warm vs 25 s
cold). The problem is only the short ones.

## 2. Why it matters

Several of the robot's calls are under 1024 tokens:

- **Photo questions** (`ask_photos`, locating an object, the arrival check) while
  the robot has fewer than about 5 photos. Each call takes 9–15 s instead of
  about 1 s, and "go near the box" makes three of them before the wheels move:
  **35–50 s of silence** on the real robot.
- **The voice "is this for me?" check**: a short prompt sent on every sentence
  heard in the room (about 1 s each, re-read every time).

## 3. The likely cause

The server runs Gemma (sliding-window attention) **without `--swa-full`**. In
that mode the sliding-window layers keep only about the last 1024 positions of
KV. To answer any new request, llama.cpp must roll the slot back at least one
token: even an identical prompt re-evaluates its last token, and an append
differs from the previous request at the token the model generated. Rolling back
sliding-window layers needs saved data (a context checkpoint) from before that
point. Our measurements fit "no usable checkpoint exists for prompts shorter than
the window, so llama.cpp re-processes the whole prompt".

This is an inference from the numbers, not read from the server source. The
server log will confirm it. When a short prompt misses, look for a line like
`forcing full prompt re-processing due to lack of cache data (likely due to SWA ...)`
and note its exact text.

Server facts as reported by its own endpoints:

- build `b9830-c1a1c8ee9`
- model `/Users/singireddy/llm_models/gemma/12B/gemma-4-12B-it-Q4_K_M.gguf`
- `total_slots: 4`, `n_ctx` per slot 16384
- `/metrics` is disabled (no `--metrics`)

## 4. What to change (in order of preference)

1. **Start with `--swa-full`.** Full-size cache for the sliding-window layers, so
   any rollback works and the 1024 cut-off disappears. It costs memory: those
   layers then keep `n_ctx` positions per slot instead of 1024. Before
   restarting, work out the KV size for 5 slots from the model's metadata, and
   check it fits the Mac's RAM next to the model weights. If it doesn't fit:
   - lower the per-slot context. The robot's largest prompts are about
     6–8k tokens (an agent's prompt with history, or 24 photos), so **keep at
     least 12288 per slot**. Note that `-c` is usually the TOTAL split across
     slots: 5 slots × 12288 = `-c 61440`. Check how this build splits it (look
     for `n_ctx_slot` in the startup log).
   - Quantising the KV cache (`--cache-type-k/-v q8_0`) would roughly halve it,
     but it stops the planned Gemma 4 draft model (MAC_MINI_TASKS.md Task 5)
     from working, so use it only as a last resort.
2. **If `--swa-full` can't fit:** look for this build's checkpoint options and
   test whether they lower the cut-off:
   `llama-server --help | grep -i -E 'checkpoint|swa|cache-reuse|cache-ram'`.
   Run the check below after each change.
3. **Last resort:** a newer llama.cpp build, if its release notes mention SWA
   checkpoint or prompt-cache fixes. Re-run every check afterwards, because the
   robot depends on per-slot caching.

## 5. Flags the robot needs (keep all of these)

- `--jinja`: tool calls depend on it.
- **`--parallel 5`** (currently 4): slots 0–2 are the three agents, slot 3 is
  photo questions, slot 4 is the voice "is this for me?" check. With 4 slots the
  voice check has no slot of its own and keeps evicting slot 3's photo cache.
- the same model file, with vision enabled (the mmproj as now).
- optional but useful: `--metrics`.

## 6. How to check it: run on the Mac, no robot needed

Save as `cache_cutoff_check.py` and run `python3 cache_cutoff_check.py`
(standard library only). It sends text prompts of rising size to slot 3, twice
each, the second with a different last few tokens.

```python
#!/usr/bin/env python3
"""Does llama-server reuse a slot's cache for SHORT prompts? PASS = every size reused."""
import json, sys, time, urllib.request

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080") + "/v1/chat/completions"
SLOT = 3

def call(text):
    body = {"messages": [{"role": "user", "content": text}], "max_tokens": 1,
            "id_slot": SLOT, "cache_prompt": True, "temperature": 0}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        tm = json.load(r).get("timings", {})
    return time.time() - t, tm.get("prompt_n"), tm.get("cache_n")

fails = 0
for facts in (20, 60, 85, 110, 160):          # ~230, ~700, ~1000, ~1300, ~2000 tokens
    pad = " ".join(f"Fact {i} concerns the number {i * 7}." for i in range(facts))
    call(f"reset {time.time()}")                                   # clear the slot
    _, n1, _ = call(pad + " Question: what is fact 3?")
    dt, recomputed, reused = call(pad + " Question: what is fact 9?")
    ok = (reused or 0) > 0.8 * n1
    fails += not ok
    print(f"{n1:5d} tokens: 2nd request {dt:4.1f} s  recomputed={recomputed:<5} reused={reused:<5} "
          f"{'OK' if ok else 'NOT REUSED'}")
print("PASS: short prompts are cached too." if not fails else
      f"FAIL: {fails} size(s) not reused -- see the server log for 'forcing full prompt re-processing'.")
sys.exit(1 if fails else 0)
```

Today (run from the Pi 5 against the Mac) it prints NOT REUSED for 233, 713
and 1013 tokens and OK for 1323 and 1990. **After the fix, every line must say OK.**

Then confirm that nothing else broke. From the Pi 5 (`~/ros2_ws`):

```bash
python3 scripts/llm_cache_check.py     # slots keep separate caches + photos cached: must PASS
```

It must print PASS twice (the text slots, then the photo step) and report 5
slots. The brain's log says `LLM server reports 5 parallel slots` at its next
start.

## 7. Please send back

1. The exact llama-server command line before and after.
2. The server log line printed when a short prompt missed, before the fix.
3. The output of `cache_cutoff_check.py` after the fix.
4. Memory use after the restart (Activity Monitor or `vm_stat`), so we know the
   headroom.

---

## 8. Resolution (Mac Mini, 2026-10-11)

**Cause: a llama.cpp bug, not missing `--swa-full`.** The inference in §3 was close but not
quite it. The verbose server log showed what happens on the 2nd short request:

```
slot operator(): id 3 | checking checkpoint with [0, 0] against 0...
slot operator(): id 3 | restored context checkpoint (pos_min = 0, pos_max = 0, n_tokens = 1, n_past = 1)
```

No "forcing full prompt re-processing" line: the server *restored* a 1-token checkpoint, which
re-reads the rest. In `tools/server/server-context.cpp` (b9830, and still on upstream master on
2026-10-11):

```cpp
const auto pos_min_thold = std::max(0, pos_next - n_swa - ...);  // 0 for any prompt < n_swa (1024)
if (pos_min >= pos_min_thold) {                                  // 0 >= 0: searches checkpoints
```

When the slot still holds every position from 0 (`pos_min == 0`), nothing has left the sliding
window and the cache is valid as it is, but `>=` sends it to the checkpoint search anyway, and
the only checkpoint is the one made after token 1. The fix is one condition:
`if (pos_min > 0 && pos_min >= pos_min_thold)`. Prompts longer than 1024 never hit it, which is
the cut-off in §1.

**Why not `--swa-full`:** Gemma 4 12B's 40 sliding-window layers are 8 KV heads x (256+256) x
f16 = **320 KB per token**, the 8 global layers 16 KB. Full-size SWA for 5 x 12288 tokens is
~20 GB of cache (3 slots: ~12 GB) on a 16 GB Mac whose GPU may use ~10.7 GB by default. It
can't fit, and with the patch it isn't needed.

**What runs now** (`scripts/mac_mini/e12b5`, started by hand after each power-on):

- llama.cpp **b9830 + `scripts/mac_mini/llama_swa_cache.patch`**, built in `~/llama.cpp-kino`
  by `scripts/mac_mini/build_llama_kino.sh` (Homebrew's unpatched build stays as fallback).
- `--parallel 5 --ctx-size 81920` (5 x 16384), `-ub 512`, `--metrics`, the other flags as before.
- GPU memory limit raised to 13312 MB (`sudo sysctl iogpu.wired_limit_mb=13312`, asked by
  `e12b5`; resets at reboot). 5 slots at `-ub 512` ran out of GPU memory at the default limit.
- Server memory 10.8 GB (7.4 model + 3.4 KV), reserved at start.
- Ollama quit and its autostart removed (it also launched its own llama-server).

**Measured after the fix:**

```
cache_cutoff_check.py                      rewind (3.2k-token prompt, slot 3)
  230 tokens: 0.6 s reused=223   OK         dropped ~152: 0.5 s OK
  710 tokens: 0.6 s reused=703   OK         dropped ~262: 0.5 s OK
 1010 tokens: 0.6 s reused=1003  OK         dropped ~372: 0.5 s OK
 1320 tokens: 0.6 s reused=1313  OK         dropped ~427: 0.5 s OK
 1987 tokens: 0.6 s reused=1980  OK         dropped ~482: 0.5 s OK
PASS: short prompts are cached too.
```

- photo question, same photo again: 9 s -> **1.6 s**.
- all 5 slots at once (4 x 1.8k text + 1 photo): all answered, no GPU out-of-memory.
- **The rewind margin is the micro-batch:** the SWA cache keeps 1024 + `-ub` tokens, so `-ub 256`
  cut it to ~160 tokens (measured: a 267-token rewind re-read 3.2k tokens, 26 s). Keep `-ub 512`.
- Not yet run: the Pi's `scripts/llm_cache_check.py` (needs a brain restart to see 5 slots).
