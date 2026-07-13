[//]: # (# LangRobo PRD — production requirements & decisions)

[//]: # ()
[//]: # (Goal: a production-grade household robot that can listen, think, talk, move)

[//]: # (and do tasks — sellable eventually, bulletproof at home first.)

[//]: # ()
[//]: # (The original brain-dump lives in `My_Goal.md`. This file is the working PRD:)

[//]: # (each requirement, what was decided &#40;grill session 2026-07-06&#41;, and where it)

[//]: # (stands. Roadmap detail: PRODUCT.md. Architecture: ARCHITECTURE.md.)

[//]: # ()
[//]: # (---)

[//]: # ()
[//]: # (## Decisions locked &#40;2026-07-06&#41;)

[//]: # ()
[//]: # (| # | Decision | Rationale |)

[//]: # (|---|---|---|)

[//]: # (| D1 | **Evolve the existing architecture** — no rewrite onto langgraph-swarm/subgraphs/deepagents | The hand-rolled supervisor+handover core IS the swarm pattern, tuned for the ≤2s voice budget on the 12B local model; extra LLM hops were already rejected &#40;PRODUCT.md&#41; |)

[//]: # (| D2 | **Person following / come-to-me deferred to phase 2** &#40;depth camera&#41; | 2D servo at a moving person bumps furniture; robot answers "coming soon" honestly &#40;same pattern as Nav2&#41; |)

[//]: # (| D3 | **Proactive vision NOW, as armed watch mode** | Flagship feature pulled forward; explicit arm/disarm because face-rec doesn't exist yet — zero false alarms while home |)

[//]: # (| D4 | **All prompts in one file** — `langrobo_core/prompts.py` | Owner preference: read every prompt in one sitting |)

[//]: # (| D5 | **Self-learning = nightly local memory consolidation** &#40;episodic → facts&#41; | Makes the robot visibly learn; monthly fine-tuning stays a reserved slot &#40;the facts collection is its future dataset&#41;; mem0/cloud memory rejected — privacy is the moat |)

[//]: # (| D6 | **Zero new cloud services** &#40;no Redis, no mem0&#41; | Local-first is the selling point; Qdrant already embedded; cloud LLM fallback stays the only cloud touchpoint |)

[//]: # (| D7 | **Deploy before building** on a verified base | Stacking unverified code on unverified code makes bugs unbisectable |)

[//]: # (| D8 | **"Sellable" this round = bulletproof at home** | PRODUCT.md's own buy-test &#40;two-week unprompted retention&#41; comes before productizing for strangers |)

[//]: # ()
[//]: # (## 1. Speech)

[//]: # ()
[//]: # (| Requirement | Status |)

[//]: # (|---|---|)

[//]: # (| Wake word &#40;"hey jarvis" → later "hey chotu"&#41; | ✅ config-only — Jetson `wake_aliases` &#40;transcript-based gate&#41;; "hey chotu" alias pending Jetson deploy &#40;TODO.md&#41; |)

[//]: # (| Listen a few seconds after speaking, no re-wake | ✅ Jetson attention window &#40;15s → 6s tune pending deploy&#41; |)

[//]: # (| Music / jokes / Q&A handled properly | ✅ music tools + chat agent; play-while-playing confirm bug fixed &#40;308fab2&#41; |)

[//]: # (| "Stop the music and …" chained commands | ✅ single utterance → agent stops music, continues with the rest |)

[//]: # (| "… and follow me / come near me" | ⏳ phase 2 &#40;D2&#41; — navigate agent explains honestly |)

[//]: # ()
[//]: # (## 2. Movement)

[//]: # ()
[//]: # (Moves on command today &#40;fine Twist + object visual-servo&#41;. Nav2/SLAM/nvblox)

[//]: # (slot is reserved and answers honestly until the depth camera lands)

[//]: # (&#40;`navigate_to_pose`, `/goal_pose`, odometry topic, locations in)

[//]: # (agent_params.yaml&#41;. Isaac ROS on the Jetson plugs into these interfaces —)

[//]: # (no brain rework needed.)

[//]: # ()
[//]: # (## 3. Vision)

[//]: # ()
[//]: # (| Requirement | Status |)

[//]: # (|---|---|)

[//]: # (| Agents act as a team on visual questions | ✅ handover protocol: any agent routes visual queries to local_agent; it looks, confirms, hands back with details |)

[//]: # (| Understand situations, send images to Telegram | ✅ `look&#40;&#41;` frames stay in conversation; `send_telegram_photo` from any agent |)

[//]: # (| Proactive: alert when someone is seen | ✅ **watch mode** &#40;D3&#41; — "watch the house" arms it; person seen → photo to owner's phone + spoken announcement; cooldown; survives restarts; no new Jetson code &#40;rides `/vision/target`&#41; |)

[//]: # (| Stranger-vs-family distinction | ⏳ needs P2 face recognition; watch mode upgrades to stranger-only on the same plumbing |)

[//]: # ()
[//]: # (## 4. Telegram)

[//]: # ()
[//]: # (✅ Shipped 2026-07-04 &#40;TELEGRAM.md&#41;: bidirectional text+photo with the same)

[//]: # (brain/history, roles &#40;owner/family/guest&#41; enforced in tools, relay + errand)

[//]: # (report-back, quiet hours. Voice and Telegram coexist by queue priority:)

[//]: # (system events → voice &#40;person in the room&#41; → Telegram. Watch mode adds)

[//]: # (arm/disarm from Telegram and photo alerts &#40;which bypass quiet hours&#41;.)

[//]: # ()
[//]: # (Coexistence decisions &#40;grill session 2026-07-06, part 2&#41;:)

[//]: # (- **D9 One shared history stays** — the robot is one household member across)

[//]: # (  both channels; someone at home texting it and someone talking to it feed)

[//]: # (  the same brain. A Telegram message during a voice conversation waits its)

[//]: # (  turn and replies silently to the sender's chat; the person in the room)

[//]: # (  never notices.)

[//]: # (- **D10 Telegram→voice announcements** &#40;`announce_at_home`&#41;: a Telegram)

[//]: # (  sender can have the robot SAY things at home &#40;"announce that dinner is)

[//]: # (  ready"&#41;. Bare "tell Mom X" → the robot asks the sender back: her phone or)

[//]: # (  aloud? Quiet hours refuse with alternatives; explicit insistence overrides.)

[//]: # (  Owner+family only. Walking to the person first remains phase 2 — it)

[//]: # (  announces from where it stands. **The ask-back is enforced in the tools**)

[//]: # (  &#40;`tools/_relay_confirm.py`&#41;, not just prompts — the 12B occasionally)

[//]: # (  skipped the question, so an unconfirmed relay cannot send at all.)

[//]: # ()
[//]: # (## 4b. New agents &#40;2026-07-06 part 3 — "multi agents, proper outputs"&#41;)

[//]: # ()
[//]: # (- **knowledge agent** &#40;ported from the SubAgents predecessor's RAG module,)

[//]: # (  adapted to embedded Qdrant + fastembed, zero new services&#41;: send the robot)

[//]: # (  a .pdf/.txt/.md on Telegram → chunked, embedded, stored locally; questions)

[//]: # (  like "what does error E4 mean on the washer?" are answered FROM the)

[//]: # (  documents, citing the source file. Re-sending replaces. CLI bulk ingest)

[//]: # (  when the brain is stopped &#40;embedded store is single-process&#41;.)

[//]: # (- **briefing agent**: scheduled morning briefing &#40;opt-in via)

[//]: # (  LANGROBO_BRIEFING_HOUR, once daily through the [SYSTEM] producer&#41; and)

[//]: # (  on-demand "give me my briefing" — reminders due today, weather, list)

[//]: # (  highlights, one flowing spoken paragraph.)

[//]: # (- From that repo also evaluated and REJECTED: Mem0 &#40;privacy moat&#41;, Redis)

[//]: # (  frame buffer &#40;look&#40;&#41;'s staleness contract is stronger&#41;, Redis web cache)

[//]: # (  &#40;queries too rare to justify a service&#41;.)

[//]: # ()
[//]: # (## 5. Architecture)

[//]: # ()
[//]: # (- Evolve, don't rewrite &#40;D1&#41;. LangGraph StateGraph + supervisor routing +)

[//]: # (  handover registry; recipes in ARCHITECTURE.md keep additions one-file-per-step.)

[//]: # (- Prompts: single `prompts.py` &#40;D4&#41;.)

[//]: # (- Pure-core / ROS split is a package boundary &#40;`langrobo_core` never imports)

[//]: # (  rclpy&#41; — the brain runs and tests anywhere.)

[//]: # (- Every service degrades, never crashes &#40;missing key → feature off&#41;.)

[//]: # (- Documentation set: HOW_IT_WORKS &#40;walkthrough&#41;, ARCHITECTURE &#40;design&#41;,)

[//]: # (  OPERATIONS &#40;run/deploy/troubleshoot&#41;, PRODUCT &#40;roadmap&#41;, TELEGRAM &#40;channel&#41;,)

[//]: # (  this PRD &#40;requirements + decisions&#41;.)

[//]: # ()
[//]: # (## 6. Self-learning)

[//]: # ()
[//]: # (Phase 1 ✅ &#40;D5&#41;: nightly consolidation — local model distills the day's)

[//]: # (episodes into durable facts &#40;`facts` Qdrant collection, deduped&#41;, recalled by)

[//]: # (`recall_memory` months later. Phase 2 &#40;reserved&#41;: monthly LoRA fine-tune on)

[//]: # (the Mac Mini consuming the same facts collection as its dataset — only worth)

[//]: # (building once consolidation shows what data is worth training on.)

[//]: # ()
[//]: # (## 7. Nav/SLAM readiness)

[//]: # ()
[//]: # (Agents + prompts are ready today: navigate agent owns the requests, answers)

[//]: # ("coming soon" for what needs hardware, and `navigate_to_pose` starts working)

[//]: # (the moment a Nav2 action server appears on the network — zero brain changes.)

[//]: # ()
[//]: # (---)

[//]: # ()
[//]: # (## Definition of done for this round &#40;D7, D8&#41;)

[//]: # ()
[//]: # (1. Pending cross-repo fix batch deployed and voice-verified on-device)

[//]: # (   &#40;TODO.md — Pi5 done 2026-07-06; Jetson pending, was offline&#41;.)

[//]: # (2. Watch mode + consolidation + prompts consolidation deployed and verified)

[//]: # (   &#40;TODO.md checklist&#41;.)

[//]: # (3. Docs current &#40;this file, ARCHITECTURE, OPERATIONS, PRODUCT, TODO&#41;.)

[//]: # (4. Two-week unprompted retention starts counting &#40;PRODUCT.md buy test&#41;.)
