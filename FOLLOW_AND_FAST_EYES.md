# Follow me, and fast eyes — the plan

Branches: Pi 5 `dev-1.6.2-follow-me` (from `dev-1.6.1`), Jetson
`~/rover` `rover-v1.1.9-follow-me` (from `rover-v1.1.8`, which is left exactly at
`origin`). Owner, 2026-10-10: "make sure the things we add improve it, not
make it worse … think of the general things users might ask … implement
after a proper plan."

Roadmap page for people: https://claude.ai/artifact/E7o4XrtZ93i6b4wVCLTope

## 0. Rules this work keeps

1. **Off by default.** Every new path is behind a switch. With the switch off,
   the robot behaves exactly as `dev-1.6.1` / `rover-v1.1.8`: no new tool
   schema in any prompt (so no KV-cache change), no new node started by
   `./rover up`.
2. **Shadow before control** where a new path replaces an old one (fast eyes,
   Phase B): the new path proposes, the old one decides, both are logged and
   compared. Only measured agreement promotes it (the Jev pattern,
   ARCHITECTURE_LLD.md §4.3c).
3. **Safety is code, not prompt.** Stopping on any utterance, teleop MANUAL,
   role permissions, pose trust and obstacle checks are enforced in tools and
   nodes. A prompt rule is a thing to measure, not a fix (CLAUDE.md).
4. **One driver on `/cmd_vel`.** follow, reach and goal_exec cancel each other;
   the brain starts at most one through the same `start_*` / `cancel_navigation`
   seam it already uses.
5. **Reuse the existing seams**: the nav-done callback → `[SYSTEM]` report
   (`movement.nav_report`), `remember_requester`, `blocked_by_role`,
   `blocked_by_manual`, `ManualWatch`. No second mechanism.
6. **Tested at three levels** before the owner sees it move: offline unit /
   closed-loop tests (both repos), a dry run on the real robot (commands to a
   topic nothing drives), then a supervised floor test. The brain's ~350 tests
   must stay green.

## 1. What people will actually say, and what must happen

### Follow / come

| They say | Channel | What happens | Where it is decided |
|---|---|---|---|
| "Mitra, follow me" / "come with me" / "follow" | voice | Follow the person nearest the middle of the view at 1.0 m until told to stop, lost, blocked or 10 min | `follow_person(mode="follow")` → Jetson follow_node |
| "come here" / "come to me" / "come closer to me" | voice | Drive to ~1.0 m from the person in view, face them, stop, report | `follow_person(mode="come")` (= follow with `stop_at_gap`) |
| "stop" / "wait" / "stay there" / anything else | voice | Wheels stop at once (every utterance already does), the follow is cancelled, nothing extra said for the cancel | agent_node `_on_user_input` → `cancel_navigation` → `/follow/cancel` |
| "follow me" with nobody in view | voice | Says it can't see anyone and to stand in front of it. Does NOT spin around looking | Jetson `nobody` result → tool's own reply (it waits for the start) |
| "follow me" while teleop is MANUAL | any | Refused with the existing MANUAL message | `blocked_by_manual()` |
| "follow me" / "come to me" | Telegram | Refused: over Telegram it cannot see who "me" is. The sender can ask it to go to a saved place instead | tool, by channel |
| "follow me" from a guest role | Telegram | Refused (no move capability) | `blocked_by_role()` |
| Person walks out of view | — | Keeps 3 s, re-finds them near where they were heading, else stops and says it lost them | Jetson follow.py → `[SYSTEM]` report |
| Something blocks the way | — | Stops, keeps facing them; after 10 s says it is blocked | Jetson follow.py → `[SYSTEM]` report |
| "follow him" / "follow the person in the red shirt" | voice | v1: follows the person nearest the middle and SAYS so. v2 (Phase B): numbered boxes → VLM picks | v1 tool docstring; v2 Phase B |
| "follow the dog" | voice | v1: says it can only follow people for now | tool |
| "follow me to the kitchen" | voice | v1: follows; the place name is ignored and it says so | tool |
| "go to Rakesh" / "go to my dad" | any | Unchanged: asks what they look like (`approach._names_a_person_only`) | existing |
| "what do you see?" during a follow | voice | The utterance stops the follow (rule 4 of the brain), then the vision answer. v1 accepts that trade; documented | existing |

### Find and go (Phase B, fast eyes; until then unchanged)

| They say | Today | With fast eyes |
|---|---|---|
| "go near the chair" (a COCO class in view) | VLM search + point | detector box → depth → reach; VLM only confirms |
| "go near the red bottle" | VLM search over up to 8 views | spin while YOLO watches; numbered boxes → VLM picks the number |
| "go near the surf excel packet" (not COCO) | VLM search | unchanged (open-vocabulary detector only after it is measured on the Jetson) |
| "how many people are in the room?" | VLM | unchanged in v1 (a count from one view is not "the room") |
| "where is my bag?" | photo log + VLM | unchanged; detector tags may later pick which photo to re-ask, never reorder the cached ones (the rewind limit, CLAUDE.md rule 2) |

## 2. Phase A — follow me, end to end (now)

### Jetson (`rover-v1.1.9-follow-me`)
- [x] `phase4/nodes/person_tracker.py` + `/vision/people` (58e3e63)
- [x] `phase3/nodes/follow.py`, `follow_node.py`, tests; dedupe; TF own node (4bd0a97)
- [ ] `./rover` stops nodes gently: `kill_match` sends SIGINT, waits, then -9.
      (2026-10-10: repeated -9 restarts left FastDDS shared memory unusable for
      every NEW process in the container; only a container restart cured it.)
- [ ] `./rover follow`: starts detections_3d if needed + follow_node, with a
      gate (follow listening, people publishing). NOT part of `./rover up`.
- [ ] `phase3/tools/virtual_person.py`: publishes `/vision/people` for a
      person standing still at a chosen odom point, computed from live `/odom`.
      Lets the real follower be floor-tested with nobody in the room, and is
      repeatable.

### Pi 5 (`dev-1.6.2-follow-me`)
- [ ] `ROS2Bridge.start_follow(mode)`: cancels any drive, publishes
      `/follow/start`, waits ≤ 5 s for the first status (so the tool can say
      "I can't see anyone" honestly), then a background worker watches
      `/follow/status` and fires the SAME nav-done callback on the end
      (reached / lost / blocked / timeout …). `cancel_navigation()` publishes
      `/follow/cancel`. `StubBridge` mirrors it (parity test).
- [ ] `tools/follow.py`: `follow_person(mode="follow"|"come", then="")` on
      `navigate`, bound only with `LANGROBO_FOLLOW=1`. Checks role, MANUAL,
      channel; `remember_requester` so the end report goes to the asker.
- [ ] navigate's routing copy and intent examples gain "follow me", "come with
      me", only when the flag is on (so the default prompt is byte-identical).
- [ ] Tests: tool refusals (role, MANUAL, Telegram, unavailable Jetson),
      start/nobody/reached/lost messages, cancel on utterance, parity,
      prompt contract with the flag on and off.

### Proof, in order
1. Unit + closed-loop tests green on both machines; brain suite green with
   the flag off AND on.
2. Dry run, real robot, virtual person: commands on `/follow/cmd_vel_dry`.
3. Floor, virtual person, slow: a turn-only case, then a ~0.5 m approach in
   open space; obstacle stop checked against a real object.
4. Brain end to end, nobody in view: "Mitra, follow me" → "I can't see anyone".
5. With the owner: real follow, come-to-me, lost, blocked. Only then is the
   switch proposed for default-on.

## 3. Phase B — fast eyes for find-and-go (planned, not started)

Shadow first: while today's approach runs, the detector proposes (class
boxes for the target noun), and the log records whether its box matches the
VLM's choice and how much sooner it was available. Then, behind
`LANGROBO_FAST_EYES=1`:
1. **Spin-search with the detector**: exact goal_exec turns while YOLO runs at
   full rate; stop on the first box of the target class.
2. **Numbered-box question**: draw numbered boxes, ask the VLM "which number
   is <the user's description>? answer with the number or none". Short answer,
   exact box. Falls back to today's point-picking when no box matches.
3. **Ground from the box** with the existing `pixel_to_goal` (box centre and
   its depth slab), then reach and the existing arrival check.
Measure: time from utterance to first wheel motion, success rate over 20
placed-object trials, against today's numbers in FIND_AND_GO.md.

## 4. Phase C onward (from the roadmap page)
Memory across reboots and places (saved maps per place, relocalise on boot);
slow down near people/pets in reach/goal_exec; room-watch alerts as a
`[SYSTEM]` producer; save VLM-confirmed crops for the learning loop.

## 5. Status log
- 2026-10-10 night: plan written; Phase A Jetson steps 1-2 done (dry runs
  with the owner); the rest of Phase A in progress. Owner allowed floor
  moves overnight (robot parked in a safe place); no person-follow test
  without the owner.
- 2026-10-10 night, built: `./rover` gentle kill_match + `./rover follow`
  + `virtual_person.py` (Jetson afbe43d); follow_node `req` echo (8a67202);
  speed governor (d3777e1); Pi `follow_person` + `start_follow` behind
  `LANGROBO_FOLLOW` (967843f). Brain suite 522 passed, switch off and on;
  prompts byte-identical with it off. A clean `./rover up` passed every layer
  (laptop RViz skipped: laptop off) and cured the shared-memory fault.
- 2026-10-10 night, floor (virtual person, real wheels, watchdog per test):
  | test | person | result |
  |---|---|---|
  | T1 turn | 1.0 m, 15 deg right | turned 9.1 deg, stopped turning inside the 6 deg band; 0 forward; moved 0.8 cm (scrub) |
  | T2 approach | 1.5 m ahead | drove 0.38 m straight (heading +0.0), peak 0.21 m/s, stopped at 1.12 m (the 0.12 m band) |
  | T3 obstacle | 2.6 m ahead | stopped 0.28 m short of a TOY CAR on the floor below the LiDAR plane, seen only by the fused nvblox map (confirmed on a camera frame); "blocked" after 10 s. Watchdog never needed |
  Backing out by goal_exec: leg 1 (0.41 m reverse) reached to 0.3 cm; leg 2
  went ~0.19 m PAST its target, then refused the forward correction
  ("obstacle 0.03 m into the 0.17 m leg"). Safe, but goal_exec normally ends
  within ~1.5 cm -- open item for the Jetson (reverse leg overshoot?). The
  rover ended ~0.17 m behind where it was parked, >= 0.50 m clear all round.
- Open before the owner test: the floor in front is cluttered (tools, a
  ruler, a toy car) -- flat things may be invisible to depth; the 6 deg turn
  band and the 0.12 m distance band may want tightening for "come to me".
