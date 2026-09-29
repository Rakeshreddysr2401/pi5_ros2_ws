# Find and go — "what do you see?" … "go near it"

**The priority flow** (owner, 2026-09-27). One page for how the robot finds an
object and drives to it, across both machines, and how to tell from the logs
which step went wrong. Keep this file true when the flow changes.

## The idea in one paragraph

Every photo the robot takes is stamped with **when** it was taken and **where
the robot was** (x, y, heading in `odom`), and the Jetson freezes **that
photo's depth** the moment it is taken. So when the vision model points at
something in a photo — even an old one, taken from somewhere else — the Jetson
can turn that pixel into a real room position (x, y). From wherever the robot
is *now*, that position gives a bearing and a distance: turn, or drive over,
look again to confirm, and go.

## The flow

```
"what do you see?"   look()  ─┬─ photo into the conversation (the model answers from it)
                              ├─ Jetson holds its depth + camera pose (hold_frame, 24 kept)
                              ├─ photos.record: stamp, pose, time, epoch   (tools/photos.py)
                              └─ background survey: every object -> room x,y -> object memory
                                                                          (tools/survey.py)
"go near it"         navigate -> approach_described_object(description)   (tools/approach.py)

 0. THE PHOTO WE TALKED ABOUT  newest 2 photos in the conversation:
                               the survey already placed a match FROM that
                               photo -> use it (no VLM call); else
                               VLM "where is <it> in THIS photo?" -> box
                               Jetson places the box with THAT photo's depth + pose
                               (photo depth gone and robot unmoved -> newest depth)
      not found in a photo ->  object memory by name (things seen long ago)
 1. GO TO WHERE IT IS          from the pose NOW:
        a) within 2.5 m        face it, look            -> there: go
        b) further / not seen  reach_and_wait to 1 m in front of it, look
                                                        -> there: go
                               not there -> 2. around THAT spot, then forget it
                               can't get there -> 2. from here, memory kept
 2. SEARCH                     8 views, 45 deg apart, each aimed from the
                               measured heading; a refused turn finishes the
                               circle the other way (from wherever it stopped)
 3. GO                         ground the fresh sighting -> reach (nav2 + exact
                               finish) in the background -> "[SYSTEM] arrived"
                               (Telegram requests: report to the phone, quiet;
                                a photo asked for mid-drive is sent on arrival)
```

## Where each piece lives

| piece | Pi 5 (`~/ros2_ws`) | Jetson (`~/rover`) |
|---|---|---|
| photo stamp + pose register | `tools/photos.py` | — |
| hold a photo's depth + pose | `bridge.hold_frame` | `phase4/nodes/pixel_to_goal.py` snapshots (24; depth-gap fallback) |
| pixel/box -> room x, y | `bridge.ground_pixel` | `pixel_to_goal.py` `_on_query` (nearest solid slab in the box) |
| background survey | `tools/survey.py` (idle only, vision-tool slot 3 — so is every `_vlm_locate`; a turn cancels it mid-photo, it resumes after) | same queries |
| object memory | `services/object_memory.py` (`~/.langrobo/object_memory.json`) | — |
| the steps above | `tools/approach.py` | — |
| drive, waited on | `bridge.reach_and_wait` | `phase3/nodes/reach_node.py` |
| drive, in background | `bridge.start_nav_to_pose` | same |
| see it | RViz: orange dots (`/brain/objects`), magenta goal arrow | `phase2/rviz/rover_live.rviz` |

## Reading a run

Brain: `journalctl -u langrobo-brain -o cat | grep -E "Invoking graph|Step message|nav done|photo survey"`

| you see | it means |
|---|---|
| `photo survey (look): 0 object(s) placed` | the photo's depth was not held — check the Jetson line below |
| `fleet.sh check`: `photo survey … errors`, 0 placed | every survey is failing (`last:` says why — a missing slot 3, the Mac down); `curl -s localhost:8090/status \| jq .runtime.photo_survey` for the counters |
| `snapshot …: no_depth_near_stamp:7112ms` (Jetson `/tmp/pixel_to_goal.log`) | the depth stream stalled at the photo; followed by `(after a depth gap, camera still)` = recovered, or `moved_before_depth` = the robot moved first |
| `query …: snapshot_expired` | more than 24 photos since, or the gap was not recovered |
| tool: `It's still where I saw it` | step 1 confirmed it |
| tool: `wasn't right where I saw it, so I'm looking around that spot` | step 1b, then 2 at that spot |
| tool: `couldn't get to that spot` | `reach_and_wait` failed; searched from here, memory kept |
| tool: `couldn't turn either way` | step 2 blocked both ways (something within ~0.35 m) |
| `nav done: … failed after retrying -- goal_exec refused …` | the final drive; `reach_node` log on the Jetson (`/tmp/reach.log`) has each attempt |

## Known limits (open)

- **Depth stalls** come from the Jetson being CPU-bound (rover OPEN_ISSUES #1).
  The gap fallback covers a still robot; a fast detector on the idle GPU and
  lighter nodes are the real cure.
- **The vision model is slow and loose** (5–40 s a call; its y is off by up to
  ~45 px, hence boxes, not points). A missed sighting in step 1 forgets a
  remembered object only after searching its spot.
- **Positions reset at every power cycle** (`odom` starts at zero where the
  robot boots). Keeping them across days needs the saved slam map loaded at
  boot — next on the list.
- The Mac's llama.cpp keeps only one slot's cache (`scripts/llm_cache_check.py`),
  so each vision call after other traffic pays a full prompt read.
