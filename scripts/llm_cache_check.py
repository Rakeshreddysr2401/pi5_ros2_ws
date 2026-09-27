#!/usr/bin/env python3
"""Does the llama.cpp server keep EACH slot's prompt cache? `llm_cache_check.py [URL]`

The brain gives every agent its own KV slot (CLAUDE.md hard rule 2) so that
chat, local_agent and navigate each keep their ~3k-token prompt prefix
cached, and a handover between them costs ~1 s instead of a full re-prefill.
That only works if the server keeps a slot's cache while ANOTHER slot is
used. This checks exactly that, on slots 2 and 3 of the live server:

    1. slot 3, cold             -> full prefill (the baseline, ~25 s)
    2. slot 3, extended          -> should reuse: prompt_n tiny, ~1 s
    3. slot 2, a different prompt
    4. slot 3 again, extended    -> PASS if it still reuses; FAIL if slot 2's
                                    request wiped it (cache_n=0, full prefill)

Measured 2026-09-27 on the Mac Mini (llama.cpp b9830, Gemma 4 12B, started
with --jinja --parallel 4): step 4 FAILED -- 29 s, cache_n=0. So every change
of agent (chat -> local_agent for a vision question, and back) re-read the
whole prompt, 15-27 s per first call. The likely cause: Gemma uses
sliding-window attention, and llama.cpp's default SWA cache is sized for
the window, not for several slots' worth of it, so serving one slot prunes
the others and they can no longer be reused. `--swa-full` gives it a
full-size cache (more memory). Restart the server with `--swa-full`, then run
this again -- PASS confirms it; a FAIL means look at the server log for
"forcing full prompt re-processing" and its stated reason.

Takes ~1-2 minutes (it pays the cold prefills on purpose). Safe to run while
the robot is up: it uses slots 2 and 3, so navigate's slot is cold after it
(the next move pays one prefill).
"""
import json
import sys
import time
import urllib.request

URL = (sys.argv[1] if len(sys.argv) > 1
       else "http://singireddys-mac-mini.local:8080") + "/v1/chat/completions"

# ~3.5k tokens: about one agent's system prompt + tool schemas.
SYSTEM = {"role": "system", "content": "Cache test. " + " ".join(
    f"Fact {i} concerns the number {i * 7}." for i in range(250))}


def call(slot: int, msgs: list) -> tuple:
    body = {"model": "x", "messages": msgs, "max_tokens": 1,
            "id_slot": slot, "cache_prompt": True}
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        timings = json.load(r).get("timings", {})
    return time.time() - t0, timings.get("prompt_n"), timings.get("cache_n")


def show(label: str, res: tuple) -> None:
    dt, prompt_n, cache_n = res
    print(f"  {label:34s} {dt:5.1f} s   recomputed={prompt_n}  reused={cache_n}")


def main() -> int:
    base = [SYSTEM, {"role": "user", "content": "hello"}]
    more = base + [{"role": "assistant", "content": "hi"},
                   {"role": "user", "content": "and then?"}]
    print(f"llama.cpp slot-cache check against {URL}")
    show("1. slot 3, cold", call(3, base))
    show("2. slot 3, extended", call(3, more))
    show("3. slot 2, another prompt", call(2, [SYSTEM, {"role": "user", "content": "other"}]))
    res = call(3, more + [{"role": "assistant", "content": "ok"},
                          {"role": "user", "content": "last"}])
    show("4. slot 3 again, extended", res)
    reused = res[2] or 0
    if reused > 1000:
        print("PASS: each slot keeps its own cache -- one slot per agent works.")
        return 0
    print("FAIL: using slot 2 wiped slot 3's cache. Every agent change re-reads the")
    print("      whole prompt. Restart llama-server with --swa-full (Gemma's")
    print("      sliding-window cache), keep --jinja --parallel 3+, and re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
