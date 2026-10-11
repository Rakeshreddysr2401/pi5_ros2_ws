# Mac Mini tasks — everything the robot needs from this machine

**For:** the agent working on the Mac Mini, `singireddys-mac-mini.local`, user `singireddy`.
**From:** the robot "Mitra"'s brain on the Raspberry Pi 5 (repo `~/ros2_ws`). Updated 2026-10-11
after a full test on the real robot.
**This Mac's job:** it serves the robot's LLM and vision model (Gemma 4 12B) with llama.cpp
`llama-server` on port 8080. The Pi calls `http://singireddys-mac-mini.local:8080`.

Work through the tasks **in order**, then fill in the report at the end.

> **Status 2026-10-11 (Mac agent):** Task 3 DONE differently -- the cause was a llama.cpp bug,
> fixed with a one-line patch; `--swa-full` can't fit (docs/mac/LLAMA_CACHE_ISSUE.md §8).
> Task 4 changed by the owner: **no autostart** -- after a power-on run `e12b5` in a Mac terminal.
> Task 1 (ssh keys) and Task 5 (draft model) not done yet. Report at the end.

## Ground rules

- **Don't change the model files** (`.gguf`, mmproj) or the port (8080).
- **Ask the owner before restarting llama-server.** The robot goes deaf for the minute or
  two the model takes to load.
- Anything needing `sudo`: show the owner the exact command and let them run it.
- Delete nothing. If you replace a launch script or plist, keep the old one as `<name>.bak`.
- If a step fails, record the exact error, stop that task, and move on.

---

## Task 1 — Let the Pi (and the MacBook) in over SSH

Today the Pi gets `Permission denied (publickey,password,keyboard-interactive)`. With access, the
Pi's agent can check the server itself instead of asking the owner.

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
grep -qF 'pi5-langrobo' ~/.ssh/authorized_keys 2>/dev/null || \
  echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMmCtBs74ieo4tgdarMI29kD53FBhU28aeA1uwigPslr pi5-langrobo' >> ~/.ssh/authorized_keys
grep -qF 'claude-macbook-air' ~/.ssh/authorized_keys 2>/dev/null || \
  echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICA5e6etKJNiV1BVG/S0rb6TvAF/cicXA39QlYCBj0RX claude-macbook-air' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Then check that nothing blocks key login:
- System Settings → General → Sharing → **Remote Login** is on, and `singireddy` is allowed.
- `ls -ld ~ ~/.ssh ~/.ssh/authorized_keys`: the home folder must not be group- or
  world-writable, or sshd ignores the key.
- `sudo sshd -T | grep -iE 'pubkeyauthentication|allowusers'` (owner runs it):
  `pubkeyauthentication yes`; if `allowusers` is set, it must include `singireddy`.

**Done when:** from the Pi, `ssh singireddy@singireddys-mac-mini.local true` succeeds with no password.

---

## Task 2 — Report how llama-server runs today (read-only)

```bash
ps -axo pid,etime,rss,command | grep -i '[l]lama'      # full command line + flags
curl -s localhost:8080/props | head -c 1500; echo
sysctl -n hw.memsize hw.model; vm_stat | head -8
launchctl list | grep -iE 'llama|llm'
ls ~/Library/LaunchAgents /Library/LaunchDaemons 2>/dev/null | grep -iE 'llama|llm'
pmset -g | grep -E ' sleep|disksleep'
```

Also find **what starts it**: a terminal, a script, a LaunchAgent plist, or an app. Give its path
and where its log goes.

Known from the Pi side: build `b9830-c1a1c8ee9`, model
`/Users/singireddy/llm_models/gemma/12B/gemma-4-12B-it-Q4_K_M.gguf`, 4 slots, 16384 context per
slot, `/metrics` off.

---

## Task 3 — Fix the cache and the slot count (the main job)

**The problem, measured from the Pi (2026-10-11):** the server **never reuses a slot's cache for
a prompt shorter than ~1024 tokens**, which is Gemma's sliding window. A 1014-token prompt is
re-read from scratch every time; a 1134-token one is reused. This applies to text and photos alike,
and even an identical request sent twice is re-read. It makes the robot's photo questions 9–15 s
instead of ~1 s, so "go near the box" is silent for 35–50 s before it moves. The full evidence, the
likely cause, and a test script are in **`docs/mac/LLAMA_CACHE_ISSUE.md`** (same repo).

**The flags the robot needs, all of them:**

| flag | why |
|---|---|
| `--swa-full` | **the fix.** Keeps the full cache for Gemma's sliding-window layers, so short prompts are reused too |
| `--parallel 5` | 5 cache slots: 0–2 the three agents, 3 photo questions, 4 the voice "is this for me?" check. Today it is 4, so the voice check lands on slot 3 and evicts the photo cache |
| `--jinja` | tool calls depend on it |
| `--mmproj <file>` | vision: keep the one used now |
| `-ngl 99` | every layer on the GPU (Metal) |
| `--host 0.0.0.0 --port 8080` | must stay reachable from the network |
| `--metrics` | lets the Pi read speed and cache numbers from `/metrics` |

Keep every other flag already there unless it conflicts.

```bash
llama-server -m <model>.gguf --mmproj <mmproj>.gguf --host 0.0.0.0 --port 8080 \
             -ngl 99 --jinja --parallel 5 --swa-full --metrics   # + existing flags
```

Steps:
1. **Memory check first.** `--swa-full` makes the sliding-window layers hold the full context for
   every slot instead of 1024 tokens, so the KV cache grows. Estimate it from the model's metadata
   (layer count, KV heads, head size, which layers are sliding-window) for 5 slots, against the
   RAM from Task 2. If it doesn't fit, **lower the context per slot, never drop slots**. The robot
   needs **at least 12288 tokens per slot**. `-c` is usually the TOTAL, split across slots, so
   5 × 12288 = `-c 61440`; confirm how this build splits it (`n_ctx_slot` in the startup log).
   Don't quantise the KV cache (`-ctk/-ctv q8_0`) as the way to save memory: it breaks Task 5's
   drafter.
2. Before restarting, save one server log line from the current setup: send a short prompt twice
   and copy the line where it says why it re-processed the whole prompt (something like
   `forcing full prompt re-processing due to lack of cache data ...`).
3. Show the owner the new command line; restart with their OK.
4. Verify on the Mac:
   - `curl -s localhost:8080/props` shows `"total_slots": 5`.
   - The server log has no errors about `swa`, memory, or mmproj.
   - `python3 cache_cutoff_check.py`: the script is in `docs/mac/LLAMA_CACHE_ISSUE.md` §6
     (standard library only). **Every line must say OK.** Today the three short sizes say NOT REUSED.
5. Tell the owner, so the Pi can run `python3 scripts/llm_cache_check.py`: it must print
   PASS twice.

**Rollback:** the old command line from Task 2.

---

## Task 4 — Survive sleep, reboot and crashes (skip any part already done)

1. **No sleep** (owner runs it): `sudo pmset -a sleep 0 disksleep 0`. Display sleep can stay on.
2. **Start at login and restart if it dies:** run the Task 3 command from a LaunchAgent,
   `~/Library/LaunchAgents/com.mitra.llama-server.plist`, with `RunAtLoad` and `KeepAlive` set to
   true, and stdout/stderr going to `~/Library/Logs/llama-server.log`. Load it with
   `launchctl bootstrap gui/$(id -u) <plist>`. Stop any old copy first: port 8080 must have
   exactly one owner.
3. FileVault is on, so the Mac doesn't log in by itself after a power cut, and LaunchAgents
   don't start until someone logs in. **Ask the owner** whether to use a LaunchDaemon instead
   (starts without login), or accept logging in by hand after a power cut.

**Done when:** after `launchctl kickstart -k gui/$(id -u)/com.mitra.llama-server` the server comes
back by itself, and Task 3's checks pass again.

---

## Task 5 — Write ~2× faster with Gemma 4's draft model (separate change, after 3 and 4)

The robot measured ~13.5 tokens/s of writing, which is most of every reply's wait. Gemma 4 has a
small co-trained drafter. It guesses the next few tokens and the 12B model checks them in one
step: the answers are the same and come faster. It needs llama.cpp b9549 or later (this server is
b9830).

1. Download the drafter matching the 12B model, without touching the 12B file:
   ```bash
   huggingface-cli download google/gemma-4-12B-it-assistant-GGUF --include "*Q8*" \
     --local-dir ~/llm_models/gemma/12B
   ```
2. Add to the Task 3 command line:
   `--model-draft <the assistant .gguf> --spec-type draft-mtp --spec-draft-n-max 3`
   Keep the KV cache at f16 (no `-ctk/-ctv q8_0`): quantised, the drafter's guesses are never
   accepted. Check that these flag names exist in this build first
   (`llama-server --help | grep -iE 'draft|spec'`) and use this build's names if they differ.
3. Check memory after load, as in Task 3.
4. Verify:
   ```bash
   curl -s localhost:8080/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"Count from one to forty in words."}],"max_tokens":200}' \
     | python3 -c 'import json,sys; t=json.load(sys.stdin)["timings"]; print(t["predicted_per_second"], "tok/s", t.get("draft_n"), t.get("draft_n_accepted"))'
   ```
   **Done when:** clearly above 13.5 tok/s (target ≥ 25), a photo request still works,
   `cache_cutoff_check.py` still says OK on every line, and the Pi's `llm_cache_check.py` still
   PASSES.
5. If it isn't faster, or anything breaks: remove the drafter flags and restart.

---

## Report back (fill in and paste to the owner)

```
TASK 1  Pi key added: yes/no   MacBook key: yes/no   Pi can ssh in: yes/no   blocker: <none/..>
TASK 2  started by: <terminal | script path | plist path | app>   log: <path>
        OLD command line: <full>
        chip / RAM total / free: <..>   sleep setting: <..>
TASK 3  log line for a short-prompt miss (before): <..>
        KV estimate for 5 slots + --swa-full: <..> GB   context per slot: <..>
        NEW command line: <full>
        total_slots: <n>   memory after load: <..>
        cache_cutoff_check.py: <paste output>
TASK 4  pmset sleep 0: done/owner-pending   LaunchAgent/Daemon: <path>   survives kickstart: yes/no
TASK 5  drafter: <file>   tok/s before/after: <..>/<..>   accepted/drafted: <..>
        photos OK: yes/no   cache_cutoff_check after: all OK / <..>
```

### Report (filled in on the Mac, 2026-10-11)

```
TASK 1  not done yet (waiting for the owner's OK)
TASK 2  started by: typed by hand in a terminal (ttys002), log in that terminal only
        OLD command line: llama-server -m .../gemma-4-12B-it-Q4_K_M.gguf --mmproj .../mmproj-gemma-4-12B-it-Q8_0.gguf
            --host 0.0.0.0 --port 8080 -ngl 99 --parallel 4 --ctx-size 65536 --jinja --flash-attn on
            --reasoning off --cache-ram 0
        chip / RAM: M4 (Mac16,10) / 16 GB, ~0.1 GB free   sleep setting: sleep 0, disksleep 0
        also running: Ollama (+ its own llama-server) -- quit, autostart removed
TASK 3  log line for a short-prompt miss: none of the 'forcing full prompt re-processing' kind;
            verbose log: "restored context checkpoint (pos_min = 0, pos_max = 0, n_tokens = 1, n_past = 1)"
            = llama.cpp bug (pos_min >= pos_min_thold with both 0), patched -- LLAMA_CACHE_ISSUE.md §8
        KV estimate for 5 slots + --swa-full: ~20 GB at 12288/slot -> not possible; not used
        NEW command line: ~/llama.cpp-mitra/build/bin/llama-server (b9830 + MITRA PATCH) ... --parallel 5
            --ctx-size 81920 -ub 512 --jinja --flash-attn on --reasoning off --cache-ram 0 --metrics
            (GPU limit 13312 MB) -- scripts/mac_mini/e12b5
        total_slots: 5 (16384 each)   memory after load: server 10.8 GB
        cache_cutoff_check.py: 230/710/1010/1320/1987 tokens all OK, 0.6 s -- PASS
        rewind: ~482 tokens dropped still reused (was ~450)
TASK 4  owner: NO autostart. After a power-on: `e12b5` in a Mac terminal (asks the password for
            the GPU limit). Runs under launchd while up (restarts on crash); `e12b5 stop|status|log`.
            pmset sleep 0: already set
TASK 5  not started
PI SIDE restart langrobo-brain + langrobo-voice (they read the slot count at start), then
            python3 scripts/llm_cache_check.py -> must PASS twice and report 5 slots
```
