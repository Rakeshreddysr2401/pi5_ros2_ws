# Find and go — "what do you see?" … "go near it"

**The priority flow** (owner, 2026-09-27). One page for how the robot finds an
object and drives to it, across both machines, and how to tell from the logs
which step went wrong. Keep this file true when the flow changes.

## The idea in one paragraph

Every photo the robot takes is logged with **when** it was taken and **where
the robot was** (x, y, heading in `odom`), and the Jetson freezes **that
photo's depth** the moment it is taken. Those photos ARE the robot's memory
(owner, 2026-10-01 -- no stored "bag at x,y" list): the vision model reads all
of them at once and says which photo shows the thing and where in it, and the
Jetson turns that box into a real room position (x, y) with that photo's depth
and pose. From wherever the robot is *now*, that position gives a bearing and
a distance: turn, or drive over, look again to confirm, and go.

## The flow

```
ANY photo            look() / search view / scan view / locate / confirm look
                       ├─ Jetson holds its depth + camera pose (hold_frame, 24 kept)
                       └─ photo log: number, image, stamp, pose, time, epoch  (tools/photos.py)
"where is my bag?"   ask_photos (any agent)                        (tools/photo_recall.py)
                       VLM over ALL logged photos -> photo n + box  (slot 3, photos cached)
                       VLM again, photo n only    -> tight box
                       Jetson places the box with photo n's depth + pose -> distance, bearing NOW
"go near it"         navigate -> approach_described_object(description)   (tools/approach.py)

 1. THE PHOTOS FIRST    ask_photos("where is <it>?"), placed from that photo:
        a) within 2.5 m        face it, look            -> there: go
        b) further / not seen  reach_and_wait to 1 m in front of it, look
                                                        -> there: go
                               not there -> 2. around THAT spot
                               can't get there -> 2. from here
        depth gone             face the photo's direction (if the robot has not
                               moved off), look
      in no photo ->           2.
 2. SEARCH                     8 views, 45 deg apart, each aimed from the
                               measured heading; each view logged and asked
                               "is <it> clearly visible in photo n?"; a refused
                               turn finishes the circle the other way
 3. GO                         ground the fresh sighting -> reach (nav2 + exact
                               finish) in the background -> "[SYSTEM] arrived"
                               (Telegram requests: report to the phone, quiet;
                                a photo asked for mid-drive is sent on arrival)
 4. ARRIVE                     code takes one photo and asks "is <it> clearly
                               visible?" (approach.arrival_check) -> the report
                               says "I can see the X in front of me, 0.5 m" or
                               "But I can't see the X in front of me now";
                               then the errand, if the request had one
                               (`then`: "...and tell me what is on it") --
                               done now / "NOT done" if the drive failed /
                               dropped if a new command cancelled it
```

While it searches, a voice turn hears what it is doing (approach._say, code
not the model, one complete utterance each): "I don't see the X from here,
so I'm looking around", "Still looking for the X", "I saw the X over there
3 min ago. Going to check."

## Where each piece lives

| piece | Pi 5 (`~/ros2_ws`) | Jetson (`~/rover`) |
|---|---|---|
| photo stamp + pose register | `tools/photos.py` | — |
| hold a photo's depth + pose | `bridge.hold_frame` | `phase4/nodes/pixel_to_goal.py` snapshots (24; depth-gap fallback) |
| pixel/box -> room x, y | `bridge.ground_pixel` | `pixel_to_goal.py` `_on_query` (nearest solid slab in the box) |
| photo log + questions | `tools/photos.py`, `tools/photo_recall.py` (`ask_photos`, `locate_in`; vision-tool slot 3, photos kept cached) | — |
| old object list (OFF) | `tools/survey.py` + `services/object_memory.py`, only with `LANGROBO_PHOTO_SURVEY=1` | same queries |
| the steps above | `tools/approach.py` | — |
| drive, waited on | `bridge.reach_and_wait` | `phase3/nodes/reach_node.py` |
| drive, in background | `bridge.start_nav_to_pose` | same |
| see it | RViz: orange dots (`/brain/objects`), magenta goal arrow | `phase2/rviz/rover_live.rviz` |

## Reading a run

Brain: `journalctl -u langrobo-brain -o cat | grep -E "Invoking graph|Step message|nav done|ask_photos"`

| you see | it means |
|---|---|
| tool: `I saw the X … (photo N), but it isn't there now` | the photos had it; the confirm look and the search around that spot did not |
| `snapshot …: no_depth_near_stamp:7112ms` (Jetson `/tmp/pixel_to_goal.log`) | the depth stream stalled at the photo; followed by `(after a depth gap, camera still)` = recovered, or `moved_before_depth` = the robot moved first |
| `query …: snapshot_expired` | more than 24 photos since, or the gap was not recovered |
| tool: `It's still where I saw it` | step 1 confirmed it |
| tool: `wasn't right where I saw it, so I'm looking around that spot` | step 1b, then 2 at that spot |
| tool: `couldn't get to that spot` | `reach_and_wait` failed; searched from here, memory kept |
| tool: `couldn't turn either way` | step 2 blocked both ways (something within ~0.35 m) |
| `[SYSTEM] … But I can't see the X in front of me now` | step 4: arrived where the sighting was, the thing is not there (moved, or the sighting was wrong -- 2026-10-02 the VLM took something by a backpack for a toy car) |
| `nav report queued -> … task='…'` | the errand that rides with this drive |
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
- **The vision model is sensitive to wording and misses flat things**: "the
  air freshener spray can" found where "the spray can" was not; a flat white
  keyboard missed. Rare false sightings are caught at step 4. A double-check
  before driving was measured and NOT kept (2026-10-02, 20/20 without it).
- **Speed**: ~45 s from "go near X" to driving when it has to turn and confirm,
  mostly the Mac's ~9 tok/s. Its cache keeps photos only within a ~450-token
  rewind (not `--swa-full`): `photo_recall.py` THE REWIND LIMIT.
- **One errand per drive**: "...then come back and tell me" keeps the "tell"
  and drops the "come back".
