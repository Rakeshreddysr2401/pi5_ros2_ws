# LangRobo — Product Direction & Roadmap

What Rakhi has to become for people to buy one and use it every day.
Written 2026-07-03; updated for the production restructure (langrobo_core/langrobo_ros).

---

## The honest market read

Every consumer home robot that led with *cuteness or mobility* died: Jibo, Kuri,
Anki Vector, Amazon Astro (quietly shelved). Every home device that survived does
**one boring job reliably, daily**: Echo (voice answers, timers, reminders),
robot vacuums (floors), security cams (watching).

So the product is not "a robot that drives around." The product is:

> **A private household member.** It knows each person in the house, remembers
> everything locally, handles the boring daily loop, and speaks up at the right
> moments — without sending a single frame or utterance to the cloud.

Local-first is not a technical preference here — it's the *selling point*. It's
the only credible answer to "why would I put a camera and open mic in my living
room," and it means no subscription and no cloud outage. Alexa can't make that
promise; that's the moat.

## What "use every day" actually means

Ranked by realistic daily frequency for a household:

| Rank | Job | Status |
|---|---|---|
| 1 | Instant answers, wake word, ≤2s voice loop | current phase |
| 2 | Timers, reminders, shopping lists — **with proactive speech** | next |
| 3 | Per-person recognition + memory ("Rakesh, your parcel came at 3") | Jetson has headroom |
| 4 | Personalized morning briefing (sees your face → your calendar/weather/reminders) | composition of 2+3 |
| 5 | **Visual household memory** — "where did I leave my keys?" | differentiator, nobody has this local |
| 6 | Home watch — person at door, unusual motion, tell me when I'm back | camera + presence sensor |
| 7 | Mobility with a job (patrol, come-when-called) | **only after depth camera** |

Rows 1–6 need zero navigation. That's the point: the buyable product exists
before the robot ever moves.

## The one architectural gap

Everything above except row 1 requires **self-initiated turns**. Today the graph
only runs when `/voice/user_input` fires. A robot that only ever responds is an
app; one that appropriately initiates is a household member.

The infra is already half there: the supervisor keeps a `[SYSTEM]` event path.
What's missing is the producers — a scheduler (reminders/timers) and event
sources (face seen, presence detected, delivery update) that inject `[SYSTEM]`
turns. This is the single most product-critical addition after the latency phase.

## Small hardware buys, ranked by ROI

1. **Far-field mic array** (~$35–70, e.g. ReSpeaker USB 4-mic or a USB conference
   speakerphone) — the single biggest UX lever. Across-the-room wake word + clean
   STT is the difference between "used daily" and "walked up to like a kiosk."
   A speakerphone with built-in AEC also unlocks barge-in later for free.
2. **Full-range speaker** (~$20–30) — voice *is* the product; a tinny voice reads
   as a toy.
3. **Pan-tilt mount for the Brio** (2 servos + bracket, ~$15, driven by the ESP32
   you already have) — camera turns toward the speaker. Cheapest perceived-life
   per dollar, and widens vision coverage for rows 5–6.
4. **State light** (LED ring, ~$5, ESP32-driven) — listening/thinking/speaking at
   a glance. Trust and interruptibility.
5. **mmWave presence sensor** (LD2410, ~$5) — "someone entered the room" triggers
   without running camera inference 24/7.
6. **Do NOT buy motors/tyres yet.** Wheels without a depth camera and SLAM is a
   demo, not a product. The ESP32 chassis already covers "can move" for later;
   mobility enters when it serves a job (patrol/come-to-you), after the depth cam.

Total ≈ $80–125 and none of it is wasted if plans change.

## The buy test

A stranger buys a product; a family *keeps using* one. The metric:

> **Two-week unprompted retention.** Do family members use Rakhi daily without
> being reminded she exists? Log per-feature usage counts (extend `/diag/timing`
> events) and let the numbers pick which features live.

## Where we are (2026-07-03, post-restructure)

Code-complete and verified off-robot; the pending step is one deploy session
(OPERATIONS.md deploy checklist) rebuilding Pi5 + Jetson together:

- Streaming TTS (first-sentence audio, `<|eou|>` protocol), stop keyword,
  wake word "Rakhi" (Jetson), KV-cache discipline (warm turns pure-decode).
- Reminders/timers (incl. recurring) + household lists/facts with proactive
  spoken announcements via the `[SYSTEM]` producer pattern.
- **Production hardening (new)**: langrobo_core/langrobo_ros split, systemd
  auto-restart, structured JSON logs with per-turn trace IDs, health/metrics
  API, validated config, LLM cloud-fallback policy, episodic memory (Qdrant +
  on-device embeddings) with `recall_memory` — schema carries `person` for
  phase 4 and a reserved collection for phase 6.

Still open from the latency work: swap the Mac Mini loop model to the 3n E4B
(12B decode speed is what blocks the ≤2s budget), tune `wake_aliases` from
real transcripts, chrony-peer Pi5↔Jetson clocks.

## Roadmap (each phase ships something a household feels)

1. **Deploy & verify** the hardened voice loop on-robot (OPERATIONS.md checklist).
2. **Hear me anywhere**: mic array + better speaker (hardware list above);
   wake-word gate already shipped, acoustics need the array.
3. **Self-initiated turns**: ✅ shipped (reminders, lists, proactive speech).
3b. **Reach me anywhere — Telegram channel**: ✅ shipped (2026-07-04).
   Bidirectional text+photo chat with the same brain, capability roles
   (owner/family/guest), relay + errand ledger ("tell Mom X and let me know
   what she says"), quiet hours, person-tagged memory.
3c. **Memory consolidation (v1.1)**: ✅ built (2026-07-06, deploy pending).
   Nightly local distillation of episodic turns into a deduped `facts`
   collection, merged into recall — the self-learning phase 1 (PRD §6).
3d. **Watch mode — vision-triggered alerts (v1.2)**: ✅ built (2026-07-06,
   deploy pending). Pulled forward without waiting for face-rec/presence by
   making it explicit-arm ("watch the house"): person seen → photo to the
   owner's phone + spoken announcement, cooldown, restart-safe, zero new
   Jetson code (rides the existing /vision/target contract). Face-rec (P4)
   upgrades it to stranger-only.
   Deliberately deferred:
   daily digest (rides on
   consolidation); WhatsApp adapter (channel layer is abstracted, Meta
   Business API costs not yet justified); DeepAgents-style background lane
   for long-horizon tasks (rejected for the realtime loop — latency);
   inbound voice notes (needs STT routing via Jetson). Mem0/cloud memory
   extraction rejected outright: household conversations never leave the
   house for bookkeeping.
3e. **Errands — Swiggy food/instamart/dineout (v1.3)**: ✅ built + verified
   live (2026-07-10). Three specialist agents on Swiggy's MCP servers
   (18/14/12 tools), one PKCE login (`scripts/swiggy_login.py`, ~5-day token,
   Telegram nudge + graceful degrade on expiry, hot re-arm without restart),
   generic MCP-provider framework (`services/mcp.py`) so movie/bus tickets
   etc. are one ProviderSpec + an agent. Runs on the DEV-tier integration
   agreement (signed 09-Jul-2026, 1-year term): the household orders on the
   owner's Swiggy account — that's the authorized scope.
   Deferred until Swiggy production access (demo video → builders@swiggy.in,
   clause 2(v) written consent): **per-user Swiggy logins** — token file
   becomes per-user (`swiggy:rakesh`, `swiggy:mom`), login script grows
   `--user`, tool calls select the token by the speaker's `sender_name`
   (already in graph state). Don't build the multi-tenant plumbing before
   Swiggy consents to multi-tenant use.
4. **Knows the family**: face recognition on Jetson + per-person memory →
   greetings, briefings, "tell Rakesh when you see him." Memory schema is
   ready (`person` field); fills the wake-word gap with gaze attention.
5. **Embodied presence**: pan-tilt tracking + state LEDs + presence sensor.
6. **Visual memory**: periodic frame snapshots indexed by Gemma → "where are my
   keys," "did I leave the stove on," door watch (`visual` collection reserved).
7. **Depth camera arrives — nav/SLAM phase**: Nav2/nvblox on Jetson, mobility
   with a job — patrol, come-when-called, follow-me. The brain's slot is ready:
   `navigate_to_pose`, `/goal_pose`, locations config, odometry topic.
