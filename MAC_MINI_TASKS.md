# Mac Mini tasks — for the agent working on this machine

**From:** the Claude Code session on the owner's MacBook Air (`Rakeshs-MacBook-Air`),
which works on the robot "Mitra" (repo `pi5_ros2_ws`).
**Machine:** this Mac Mini — `singireddys-mac-mini.local`, user `singireddy`.
**Its role:** it serves the robot's LLM + vision model with **llama.cpp
`llama-server` on port 8080**. A Raspberry Pi 5 (the robot's brain) calls it
over the home network as `http://singireddys-mac-mini.local:8080`.

Work the tasks **in order**. Report back using the template at the end.

## Ground rules

- **Do not change the model files** (`.gguf` / mmproj) or the port (8080).
- **Before restarting llama-server, ask the owner** — the robot may be in use,
  and a restart drops it offline for the minute or two the model takes to load.
- Anything needing `sudo` → show the owner the exact command and let them run it.
- Don't delete anything. If you replace a launch script/plist, keep the old one
  as `<name>.bak`.
- If a step fails, stop that task, record the exact error, and move on.

---

## Task 1 — Let the MacBook in over SSH (needed first)

The MacBook has a key but the Mini rejects it (`Permission denied (publickey,…)`).
Remote Login is already on (port 22 answers).

1. Add this **public** key for user `singireddy` (safe to share; one line, exactly):

   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   grep -qF 'claude-macbook-air' ~/.ssh/authorized_keys 2>/dev/null || \
     echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICA5e6etKJNiV1BVG/S0rb6TvAF/cicXA39QlYCBj0RX claude-macbook-air' >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

2. Check that nothing blocks key login:
   - `sudo sshd -T | grep -iE 'pubkeyauthentication|authorizedkeysfile|allowusers'`
     → `pubkeyauthentication yes`; if `allowusers` is set it must include `singireddy`.
   - System Settings → General → Sharing → **Remote Login**: on, and
     `singireddy` is allowed ("All users" or listed).
   - `ls -ld ~ ~/.ssh ~/.ssh/authorized_keys` — home must not be group/world
     writable, or sshd ignores the key.

3. **Also add the robot's Pi 5 key** (added 2026-10-07 — lets the Pi's
   Claude session check the server itself instead of asking the owner):

   ```bash
   grep -qF 'pi5-langrobo' ~/.ssh/authorized_keys 2>/dev/null || \
     echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMmCtBs74ieo4tgdarMI29kD53FBhU28aeA1uwigPslr pi5-langrobo' >> ~/.ssh/authorized_keys
   ```

**Done when:** the owner says the MacBook can run `ssh mac-mini` with no password.
(The MacBook side will test it; nothing else needed here.)

---

## Task 2 — Report how llama-server runs today (read-only)

Collect, don't change:

```bash
ps -axo pid,etime,rss,command | grep -i '[l]lama'          # full command line + flags
llama-server --version 2>&1 | head -3                        # or the binary path from ps
curl -s localhost:8080/v1/models | head -c 1500; echo
curl -s localhost:8080/props | head -c 1500; echo
curl -s localhost:8080/slots | head -c 600; echo            # may be disabled — say so
sysctl -n hw.memsize hw.model; vm_stat | head -5
launchctl list | grep -iE 'llama|llm'                        # is it a LaunchAgent?
ls ~/Library/LaunchAgents /Library/LaunchDaemons 2>/dev/null | grep -iE 'llama|llm'
pmset -g | grep -E ' sleep|disksleep|displaysleep'
```

Also find **how it gets started**: a terminal someone typed into, a shell
script, a LaunchAgent plist, or an app (LM Studio / Ollama wrapper, etc.).
Give the path of whatever starts it.

---

## Task 3 — Fix the flags (the main job)

The robot needs llama-server started with **all** of these:

| flag | why |
|---|---|
| `--parallel 4` | 4 KV-cache slots: 0 = chat agent, 1 = vision agent, 2 = navigate agent, 3 = photo-survey tool. Fewer → agents evict each other every turn (~20 s re-read each time) |
| `--jinja` | needed for tool calls / streamed tool calls |
| `--swa-full` | **the fix we want to test.** The model (Gemma, sliding-window attention) currently keeps only ONE slot's cache: using one slot wipes the others, so every change of agent costs 15–27 s. `--swa-full` gives the full-size cache per slot |
| `--mmproj <file>` | vision projector — the robot sends photos. Keep whatever is used now |
| `-ngl 99` | all layers on the GPU (Metal) |
| `--port 8080`, `--host 0.0.0.0` | must stay reachable from the network (it is today — keep it) |

Keep every other flag that is already there (context size, model path, etc.)
unless it conflicts.

Reference shape:
```bash
llama-server -m <model>.gguf --mmproj <mmproj>.gguf --host 0.0.0.0 --port 8080 \
             -ngl 99 --parallel 4 --jinja --swa-full   # + existing flags
```

Steps:
1. Work out the new command line from Task 2's output; show it to the owner.
2. **Memory check:** `--swa-full` uses more memory. Estimate it against free RAM
   (Task 2). If it looks tight, say so — the fallback is a smaller context
   size, not dropping `--parallel 4`.
3. With the owner's OK, restart with the new flags.
4. Verify:
   - `curl -s localhost:8080/v1/models` → `"multimodal"` appears in capabilities
   - `curl -s localhost:8080/props` → `total_slots` is **4**
   - the server log has no errors about `swa`, memory, or mmproj
   - a quick test:
     ```bash
     curl -s localhost:8080/v1/chat/completions -H 'Content-Type: application/json' \
       -d '{"messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}'
     ```
5. Save the server log path so it can be read later.

The MacBook will then run the real pass/fail cache test
(`scripts/llm_cache_check.py`) against it — you don't need to.

---

## Task 4 — Make it survive sleep and reboot

Past outages were "the Mac fell asleep" or "llama-server wasn't running after
a restart".

1. **No sleep** (owner runs it, needs sudo): `sudo pmset -a sleep 0 disksleep 0`
   (display sleep is fine to leave on).
2. **Start at login and restart if it dies:** if it isn't already, run
   llama-server from a LaunchAgent, `~/Library/LaunchAgents/com.mitra.llama-server.plist`,
   with the Task 3 command, `RunAtLoad` = true, `KeepAlive` = true, and
   `StandardOutPath` / `StandardErrorPath` pointing at a log file
   (e.g. `~/Library/Logs/llama-server.log`). Load it with
   `launchctl bootstrap gui/$(id -u) <plist>`.
   Don't start a second copy — stop the old one first (port 8080 must have one owner).
3. Also make sure the user logs in automatically after a power cut (System
   Settings → Users & Groups → automatic login), or LaunchAgents never start —
   **ask the owner first**, it's a security trade-off. If they say no, a
   LaunchDaemon is the alternative (starts without login).

**Done when:** after `launchctl kickstart -k gui/$(id -u)/com.mitra.llama-server`
the server comes back by itself and Task 3's checks pass again.

---

## Task 5 — Make it write ~2× faster: Gemma 4's own draft model (MTP)

Added 2026-10-07 (MITRA_2_PLAN.md §1B). The robot measured **13.5 tokens/s**
of writing; that is most of every wait. Gemma 4 ships a small co-trained
"assistant" drafter: it guesses the next few tokens and the 12B model checks
them in one go. Same answers, reported 2–3× faster on Apple Silicon. Needs
llama.cpp **b9549+** (this server is b9830 — OK).

Do this AFTER Task 3/4, as its own change, so a problem is easy to pin on it.

1. Download the drafter that matches the 12B model (do not touch the 12B file):
   ```bash
   huggingface-cli download google/gemma-4-12B-it-assistant-GGUF --include "*Q8*" \
     --local-dir ~/llm_models/gemma/12B
   ```
2. Add to the Task 3 command line (keep everything else):
   ```
   --model-draft ~/llm_models/gemma/12B/<the downloaded assistant .gguf> \
   --spec-type draft-mtp --spec-draft-n-max 3 --metrics
   ```
   - **Do NOT** quantize the KV cache (`-ctk q8_0` / `-ctv q8_0`): with it the
     drafter's guesses are never accepted (known bug) — f16 KV, the default.
   - `--metrics` exposes `/metrics` (tokens/s, draft acceptance) for the robot.
3. Memory: the Q8 drafter is small next to the 12B model, but check free RAM
   after load as in Task 3. If tight, keep `--parallel 4` and lower the
   context size, never drop slots.
4. Verify:
   ```bash
   curl -s localhost:8080/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"Count from one to forty in words."}],"max_tokens":200}' \
     | python3 -c 'import json,sys; t=json.load(sys.stdin)["timings"]; print(t["predicted_per_second"], "tok/s", t.get("draft_n"), t.get("draft_n_accepted"))'
   curl -s localhost:8080/metrics | grep -iE 'tokens_predicted|draft' | head
   ```
   **Done when:** tok/s is clearly above 13.5 (target ≥ 25) with the
   drafter loaded, photos still work (send one in a request), and from the
   Pi `python3 scripts/llm_cache_check.py` still PASSES.
5. If speed does not improve or anything breaks: remove the three flags,
   restart — that is the whole rollback.

---

## Report back (paste this, filled in, to the owner)

```
TASK 1  ssh key added: yes/no   sshd blocks: none / <what>
TASK 2  started by: <terminal | script path | plist path | app>
        OLD command line: <full>
        llama.cpp version: <build>   model: <file>   mmproj: <file>
        RAM total/free: <..>         sleep setting: <..>
TASK 3  NEW command line: <full>
        total_slots: <n>   multimodal: yes/no   test reply: <..>
        memory after load: <..>      log path: <..>
        problems: <none / ..>
TASK 4  pmset sleep 0: done/owner-pending   LaunchAgent: <path>, KeepAlive yes/no
        auto-login: yes/no/declined   survives kickstart: yes/no
TASK 5  drafter file: <..>   tok/s before/after: <..>/<..>   draft acceptance: <..>
        photos still OK: yes/no   cache check from the Pi: PASS/FAIL
```
