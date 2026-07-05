# LangRobo PRD — production requirements & decisions

Goal: a production-grade household robot that can listen, think, talk, move
and do tasks — sellable eventually, bulletproof at home first.

The original brain-dump lives in `My_Goal.md`. This file is the working PRD:
each requirement, what was decided (grill session 2026-07-06), and where it
stands. Roadmap detail: PRODUCT.md. Architecture: ARCHITECTURE.md.

---

## Decisions locked (2026-07-06)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Evolve the existing architecture** — no rewrite onto langgraph-swarm/subgraphs/deepagents | The hand-rolled supervisor+handover core IS the swarm pattern, tuned for the ≤2s voice budget on the 12B local model; extra LLM hops were already rejected (PRODUCT.md) |
| D2 | **Person following / come-to-me deferred to phase 2** (depth camera) | 2D servo at a moving person bumps furniture; robot answers "coming soon" honestly (same pattern as Nav2) |
| D3 | **Proactive vision NOW, as armed watch mode** | Flagship feature pulled forward; explicit arm/disarm because face-rec doesn't exist yet — zero false alarms while home |
| D4 | **All prompts in one file** — `langrobo_core/prompts.py` | Owner preference: read every prompt in one sitting |
| D5 | **Self-learning = nightly local memory consolidation** (episodic → facts) | Makes the robot visibly learn; monthly fine-tuning stays a reserved slot (the facts collection is its future dataset); mem0/cloud memory rejected — privacy is the moat |
| D6 | **Zero new cloud services** (no Redis, no mem0) | Local-first is the selling point; Qdrant already embedded; cloud LLM fallback stays the only cloud touchpoint |
| D7 | **Deploy before building** on a verified base | Stacking unverified code on unverified code makes bugs unbisectable |
| D8 | **"Sellable" this round = bulletproof at home** | PRODUCT.md's own buy-test (two-week unprompted retention) comes before productizing for strangers |

## 1. Speech

| Requirement | Status |
|---|---|
| Wake word ("hey jarvis" → later "hey chotu") | ✅ config-only — Jetson `wake_aliases` (transcript-based gate); "hey chotu" alias pending Jetson deploy (TODO.md) |
| Listen a few seconds after speaking, no re-wake | ✅ Jetson attention window (15s → 6s tune pending deploy) |
| Music / jokes / Q&A handled properly | ✅ music tools + chat agent; play-while-playing confirm bug fixed (308fab2) |
| "Stop the music and …" chained commands | ✅ single utterance → agent stops music, continues with the rest |
| "… and follow me / come near me" | ⏳ phase 2 (D2) — navigate agent explains honestly |

## 2. Movement

Moves on command today (fine Twist + object visual-servo). Nav2/SLAM/nvblox
slot is reserved and answers honestly until the depth camera lands
(`navigate_to_pose`, `/goal_pose`, odometry topic, locations in
agent_params.yaml). Isaac ROS on the Jetson plugs into these interfaces —
no brain rework needed.

## 3. Vision

| Requirement | Status |
|---|---|
| Agents act as a team on visual questions | ✅ handover protocol: any agent routes visual queries to local_agent; it looks, confirms, hands back with details |
| Understand situations, send images to Telegram | ✅ `look()` frames stay in conversation; `send_telegram_photo` from any agent |
| Proactive: alert when someone is seen | ✅ **watch mode** (D3) — "watch the house" arms it; person seen → photo to owner's phone + spoken announcement; cooldown; survives restarts; no new Jetson code (rides `/vision/target`) |
| Stranger-vs-family distinction | ⏳ needs P2 face recognition; watch mode upgrades to stranger-only on the same plumbing |

## 4. Telegram

✅ Shipped 2026-07-04 (TELEGRAM.md): bidirectional text+photo with the same
brain/history, roles (owner/family/guest) enforced in tools, relay + errand
report-back, quiet hours. Voice and Telegram coexist by queue priority:
system events → voice (person in the room) → Telegram. Watch mode adds
arm/disarm from Telegram and photo alerts (which bypass quiet hours).

## 5. Architecture

- Evolve, don't rewrite (D1). LangGraph StateGraph + supervisor routing +
  handover registry; recipes in ARCHITECTURE.md keep additions one-file-per-step.
- Prompts: single `prompts.py` (D4).
- Pure-core / ROS split is a package boundary (`langrobo_core` never imports
  rclpy) — the brain runs and tests anywhere.
- Every service degrades, never crashes (missing key → feature off).
- Documentation set: HOW_IT_WORKS (walkthrough), ARCHITECTURE (design),
  OPERATIONS (run/deploy/troubleshoot), PRODUCT (roadmap), TELEGRAM (channel),
  this PRD (requirements + decisions).

## 6. Self-learning

Phase 1 ✅ (D5): nightly consolidation — local model distills the day's
episodes into durable facts (`facts` Qdrant collection, deduped), recalled by
`recall_memory` months later. Phase 2 (reserved): monthly LoRA fine-tune on
the Mac Mini consuming the same facts collection as its dataset — only worth
building once consolidation shows what data is worth training on.

## 7. Nav/SLAM readiness

Agents + prompts are ready today: navigate agent owns the requests, answers
"coming soon" for what needs hardware, and `navigate_to_pose` starts working
the moment a Nav2 action server appears on the network — zero brain changes.

---

## Definition of done for this round (D7, D8)

1. Pending cross-repo fix batch deployed and voice-verified on-device
   (TODO.md — Pi5 done 2026-07-06; Jetson pending, was offline).
2. Watch mode + consolidation + prompts consolidation deployed and verified
   (TODO.md checklist).
3. Docs current (this file, ARCHITECTURE, OPERATIONS, PRODUCT, TODO).
4. Two-week unprompted retention starts counting (PRODUCT.md buy test).
