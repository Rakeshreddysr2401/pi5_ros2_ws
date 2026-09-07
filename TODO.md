# TODO — pending on-device work

## RESOLVED 2026-09-04 (evening): STT VAD-silence could not be reproduced

Update: in an evening live session, both `local` (English→English) and
`sarvam` (Telugu→English) STT ran cleanly across **many consecutive
utterances** — the "worked once then silent" symptom did not recur. Added
`[diag]` logging (audio-callback heartbeat + `voiced` state; ALSA `status`
promoted `.debug()`→`.warning()`); the callback fired continuously and VAD
tracked speech normally throughout. Leading theory: the morning's in-callback
8s Sarvam timeout corrupted the PortAudio stream and the resulting ALSA input
overflow was swallowed at `.debug()`. Both are now addressed (worker-thread
transcribe in `3f3395b`; visible overflow warnings). Sarvam's success path is
confirmed (HTTP 200 + live speech). Remaining follow-ups: (a) remove the
`[diag]` lines after a few more clean multi-day sessions; (b) exercise the
wake-word gate through translation (say "Rakhi …" and confirm
`/voice/user_input`, not just `/voice/debug_transcript`). The original
morning writeup and diagnostic playbook are kept below in case it recurs.

<details><summary>Original writeup + diagnostic playbook (kept in case it recurs)</summary>

Full writeup in PI5_VOICE.md. Summary of the morning's live testing, in order:

1. **First real utterance ("Rakhi, ఇవాళ టైమ్ ఎంత") worked end-to-end**,
   through the actual mic, actual VAD, actual `agent_node`: Sarvam itself
   timed out (`ConnectTimeoutError`, 8s) but the fallback caught it cleanly
   and local Whisper translated it correctly — "what is the time today" —
   published to `/voice/user_input`, picked up by `agent_node`, which then
   hit a *separate* pre-existing issue (Mac Mini LLM unreachable) and
   degraded correctly (spoke the offline apology, didn't crash).

2. **Diagnosed why Sarvam itself timed out despite fine connectivity**
   (`curl` to the same endpoint from the same box: 0.19s) — the blocking
   8s network call was running **inside the sounddevice audio callback
   thread**, which can stall/corrupt the PortAudio stream if a callback
   doesn't return promptly. Fixed (commit `3f3395b`): `_on_audio` now only
   does VAD + framing; a background worker thread does the actual
   `transcribe()` call via a queue handoff.

3. **After the fix, deployed and relaunched clean — but no utterance since
   has triggered VAD at all**, not even a fallback/reject log line, across
   several attempts. Isolated step by step:
   - Raw `arecord` on the same hardware, run *between* attempts, captured
     clear real speech (RMS jumped from ~15–90 ambient to 422 mid-utterance,
     max amplitude 3075/32767) — **the mic and ALSA path are fine.**
   - `pw-record` against the same PipeWire source produced empty (44-byte,
     header-only) files on every attempt — but this is very likely a
     `pw-record` tool issue, not signal — the ALSA-level test above
     confirms real signal reaches the hardware layer fine, so this is a
     red herring, not the STT node's problem.
   - The threading fix (#2) did **not** resolve this — it recurred on a
     freshly-launched node, ruling out "stale/corrupted stream from the
     earlier stall" as the sole explanation.
   - `--log-level debug` on `stt_node` produced nothing per-attempt either
     — but `_on_audio` has no per-frame logging today, so this doesn't
     distinguish "callback never fires" from "callback fires, VAD just
     never classifies it as voiced" from "captures the wrong device
     silently." No conclusion reached; ran out of synchronous back-and-forth
     time to keep isolating live.

**Next step, in order of cheapest-first:**
- Add temporary logging in `_on_audio` — log `voiced` and a running
  frame-count once, or on every Nth frame, to see whether the callback is
  even firing during a real attempt (rules callback-not-firing in/out).
- If it's firing but never `voiced`: try `vad_aggressiveness: 0` or `1`
  (currently 2) — webrtcvad's classifier can reject a speaker/mic gain
  combination even at moderate settings; the round-trip test earlier today
  (weaker signal, RMS ~68) *did* trigger VAD, so a strong live-speech signal
  (RMS ~422) failing to trigger is the specific thing to explain.
- Confirm `_find_device` is resolving to the same index run over run — log
  `sd.query_devices()` in full at startup, not just the matched index,
  since `arecord -l` numbering and `sd.query_devices()` numbering come from
  different enumerations and could silently drift.
- Once real speech round-trips reliably, retest Sarvam specifically (only
  one attempt has actually reached it, and that one timed out) and Soniox
  (never reached at all — no key yet, see below).

(Original deletion criterion — "a real Telugu utterance reliably reaches STT
on repeat attempts, not just once" — is now met on `/voice/debug_transcript`;
see the RESOLVED note above. Kept for history until the `[diag]` lines are
removed.)

</details>

## OUTSTANDING 2026-09-04: Soniox STT provider needs a real API key to verify

`stt_providers/soniox.py` (PI5_VOICE.md has the design + why) is written
against the published WebSocket docs but never run against a real session —
no key available yet, and blocked on the VAD issue above anyway. Sarvam's
REST path is lower-risk (simple POST, fetched straight from current docs)
and reached the network successfully once (timed out, but connected and got
a real response back from `curl` independently) — still needs one actual
2xx response to call it verified. Both correctly degrade to local when no
key is set (verified for both: `<provider> unavailable at startup (... not
set); using local`).

**Next step:** once the VAD issue above is fixed, retry with `stt_provider:
sarvam`, watch for a real transcript vs. another timeout/fallback. For
Soniox specifically, once a key exists, watch the log for whether the
token-joining in `_run()` produces correctly-spaced text — that logic is
unverified. Delete this section once one real Telugu utterance round-trips
correctly through at least one cloud provider (not the fallback).
confirm `/voice/user_input` gets a clean transcript. Delete this section
once that's done and the wake_aliases list (`rakhi`/`chotu`/`hey pi` —
config/voice_params.yaml) has been tuned against a few real tries.

## OUTSTANDING 2026-07-10: Mac Mini llama.cpp returns "Compute error" on EVERY request

Found while verifying the day's deploys: `/health` says ok and `/slots` lists
5 slots (32k ctx each, Gemma 12B Q4_K_M, build b9830), but every completion —
every slot, no slot pin, even a 3-token prompt on the native `/completion`
endpoint — fails `500 Compute error`. So it is NOT the 5th slot / not our
request shape: the server's decode path is broken outright (likely the binary
rebuilt/updated alongside the `--parallel 5` restart). Until fixed the robot
speaks the offline degradation on every turn. Fix on the Mac: check the
server log's first error after any request (Metal/ggml message), try the
previous binary with the same flags. Verify with:
`curl http://singireddys-mac-mini.local:8080/completion -d '{"prompt":"hi","n_predict":2}'`
Then run: one voice turn, a 1-min reminder with telegram_recipient, and the
KV replay (below). Delete this section when done.

## OUTSTANDING 2026-07-10: Pi5↔Jetson ethernet cable is carrier-only (no data)

Reconnected today, link LEDs up, routes present — but the Jetson NIC shows
rx_packets=0 since boot and ARP stays INCOMPLETE both ways. Replace/reseat
the cable. Software is already prepared for it (NETWORKING.md 2026-07-10
section): once the cable passes data, `fleet_role.sh voice restart` on the
Jetson picks it automatically. Delete when the cable works.

## Investigate: supervisor never reuses its KV slot on [SYSTEM] turns

Found 2026-07-06 ~04:45 while verifying the 4-slot map. Clean consecutive
reminder cycles, `/slots` `n_prompt_tokens_processed` per slot:
chat=24, local_agent=37, specialists=150 (all reusing) — **supervisor=1791,
a FULL prefill every [SYSTEM] turn** (~15s of the ~50s system-turn cost).
Grammar/tool_choice is NOT the cause (A/B'd directly: identical repeat 5s,
appended tail 5.5s, both reused).
User-facing impact: proactive announcements (reminders/watch alerts) take
~20s+; user turns are unaffected (1.6s warm).

**Update 2026-07-10 (offline investigation from the laptop — robot was down):
could NOT reproduce with current code.** `scripts/kv_replay_supervisor.py`
(since deleted — `git show b2bf231^:scripts/kv_replay_supervisor.py`) drove
the real graph through two consecutive [SYSTEM] reminder turns
with a production-shaped history and proves BOTH halves behave:

- Client side: consecutive supervisor request bodies are strictly append-only
  (identical body fields; call 2 = call 1's messages + routing note + chat
  reply + new [SYSTEM] msg). Verified with tool-call turns, a camera frame,
  and mid-history routing notes in the history.
- Server side: replaying those payloads against the LIVE Mac Mini on
  id_slot=3 reuses fine — A repeat: prompt_n=32, B: prompt_n=180 (tail only),
  WITH grammar-forced tool_choice and mid-history system-role notes.
- Bonus datum: the cold replay showed `cache_n=589` — production slot 3 held
  a cache sharing exactly the static supervisor prefix (prompt + tool
  schemas), suggesting the production divergence started where HISTORY
  begins, i.e. 1791 ≈ the history portion, not literally byte 0.

Ruled out: registry ordering, projection append-only violations, trim_history,
the Gemma template's mid-history system-role handling, grammar × cache, slot
ctx overflow (n_ctx_slot=86k). The 07-06 observation therefore needs a
production ingredient the sim lacks — or was fixed by a commit since 07-06.

**Next step, updated 2026-09-07 for the 1.3.0 layout.** `kv_replay_supervisor.py`
and reminders (which produced the [SYSTEM] turns above) are both gone. The
[SYSTEM] turn producer in this build is **navigation arrival**, so:

1. Start llama.cpp with `--parallel 3` and confirm the brain logs
   `KV slot map (one per agent): {'chat': 0, 'local_agent': 1, 'navigate': 2}`.
2. Drive two goals in a row (`"go to the kitchen"`, wait for arrival, repeat).
   Each arrival is a [SYSTEM] turn, which now enters at **chat**.
3. `curl http://singireddys-mac-mini.local:8080/slots` and read
   `n_prompt_tokens_processed` for chat's slot 0. Reuse looks like the tail
   only (tens of tokens); the 07-06 fault looked like ~1791, a full prefill.

   **NB 2026-09-07: this may already be moot.** The agent that showed the
   fault was the supervisor, and it is gone — [SYSTEM] turns now enter at
   chat, which is warm from ordinary use. If chat's slot reuses on a nav
   arrival, the symptom cannot recur in the shape it was found.
4. If it full-prefills, capture the real request bodies with a logging proxy
   on `base_url` and diff two consecutive [SYSTEM]-turn calls — the first
   differing message is the answer. The 07-10 investigation ruled out
   registry ordering, projection, trim_history, the Gemma template's
   mid-history system-role handling, grammar × cache, and ctx overflow.
5. If it reuses: it was fixed somewhere in 07-06..today — delete this section.

Worth doing anyway: a [SYSTEM] turn is one the user did not initiate, so a
full prefill there is latency nobody waits through and therefore nobody
notices — which is exactly how it went unmeasured for two months.
