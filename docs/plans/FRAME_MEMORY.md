# Frame memory: ask about anything it has seen, and go there (0.3.0)

Owner, 2026-11-20/21: "in my home I can ask it any question; it needs to find the answer and be able
to move there", "use EmbeddingGemma 2 effectively", "accuracy first, Gemma only when not sure",
"every change in the frame saved with time and position, so the object's position is
x1 + Δx, y1 + Δy from where it was seen, wherever the robot is now".

## 1. What was measured before building (bake-off, 2026-11-20, laptop `~/bakeoff/`)

One room (owner's bedroom), teleop tour, 35 keyframes picked by the keyframe rule, 17 things present +
8 absent for search, 20 present + 16 absent photo cases for finding. Small test: read as "clearly
better", not exact percentages.

| job | candidate | result |
|---|---|---|
| search "where did you see X" | **EmbeddingGemma 2** (768, whole frame + 4 quarters) | first 15/17, top-3 16/17, false "saw it" 0/8, 44 ms/image (laptop GPU) |
| | SigLIP 2 so400m | 15/17, 17/17, 0/8 (tie; re-test on the bigger tour) |
| | Gemma 4 captions + bge-small (the brain's way before) | 12/17, 14/17, 1/8, ~27 s per frame |
| box the thing in a photo | Gemma 4 12B alone | "found" 18/20 but MADE UP 11/16 absent; boxes often on the wrong spot |
| | OWLv2 base | 13/20 at 0.25, false 1/16; tight boxes, low scores on small things |
| | YOLOE-11l | 8/20 -- dropped |
| check a box | EmbeddingGemma 2 crop vs words (margin over ~40 unrelated photos) | real vs made-up separated with AUC 0.94 (Gemma boxes), 0.88 (OWLv2 boxes) |
| **chosen chain** | OWLv2 top-5 + Gemma box -> EG2 margin; >= 0.15 accept, 0.06-0.15 Gemma yes/no on a close-up, < 0.06 next frame | **15/20 found, every accepted box on the right thing, 0/16 made up** |

Where it can run (same model file = same quality everywhere): Pi 5 4.6 s/image using all 4 cores
(too slow, steals the brain's CPU; text queries 0.13 s are fine); Jetson starved navigation
(2026-10-29: free RAM 2.2 -> 0.86 GB, load 29 -> 39); laptop GPU 0.044 s (dev machine, not always on);
**Mac mini** (always on, next to Gemma 4): the home, CPU/Metal speed and memory to be measured
(Gemma leaves ~12 % RAM free).

## 2. The pipeline

```
JETSON  frame_capture (no model)            every keyframe: moved 0.40 m / turned 20 deg / scene changed /
        ├─ colour JPEG (image_bridge)        30 s still
        ├─ aligned depth (16-bit PNG)  ─────── stays on the Jetson: ~/rover/logs/frames/
        ├─ K, camera pose in odom AT THE STAMP (TF), map->odom at the stamp, room, origin_epoch
        └─ /frames/new ──────────────────────▶ MAC memory service
MAC     memory service                       EG2 (768) on whole + 4 quarters -> `frames` store
        search / verify / objects            (JPEG kept on the Mac, depth NOT)
PI 5    brain: find_out / approach           "where is my helmet" -> Mac search -> top frames (spread by place)
                                             -> boxes (OWLv2 + Gemma) -> EG2 check -> Gemma yes/no if unsure
                                             -> JETSON pixel_to_goal grounds the box on THAT frame's own depth
                                                and pose (from disk, any age) -> object in map + odom
                                             -> object memory (fused sightings) -> nav2 -> fresh look -> answer
```

Why the depth stays on the Jetson: pixel_to_goal already grounds a box correctly (nearest solid slab,
centre band, LiDAR cross-check, the photo's own stamp and pose). One implementation of that math; it
only learns to read a remembered frame from disk instead of its 2 s ring.

Positions: the capture stores the camera pose in odom AND map->odom at that moment, so a frame from an
earlier power-on still lands right on the house map (object memory already keeps things in the house
map since 2026-11-16).

## 3. EmbeddingGemma 2's jobs (one model, one vector space)

| # | job | compares | status |
|---|---|---|---|
| 1 | memory search | words <-> frame pieces | measured (above) |
| 2 | box check | words <-> crop | measured (above) |
| 3 | same object or another? | crop <-> crops in object memory (+ position) | to measure: two similar bottles |
| 4 | what changed | new frame <-> old frame, same viewpoint | to measure: move things between two tours |
| 5 | which room | frame <-> room examples | to measure: house tour |
| 6 | "my" things | crop <-> the owner's example crop | to measure |
| 7 | facts people say | words <-> words (replaces bge-small) | later |

## 4. Steps and gates (nothing goes on before its gate)

| step | what | gate |
|---|---|---|
| 0 | base: camera stable, 2 nav goals | 0 camera stalls in 1 h; 2/2 goals |
| 1 | Jetson `frame_capture` | every keyframe has JPEG + depth + pose at stamp; a thing placed in view while parked is saved within 2 s; Jetson load/CPU cost measured |
| 2 | Mac memory service + EG2 | 2-room tour, 20 questions: top-3 >= 90 %, false "saw it" 0; Gemma unaffected (latency) |
| 3 | find + check chain in the brain | 20 absent: 0 made up; >= 85 % of placed objects found |
| 4 | pixel_to_goal grounds remembered frames; object memory fuses sightings | 5 tape-measured objects seen from 3 places: median < 10 cm, worst < 20 cm |
| 5 | `find_out` ask -> go -> answer | owner's 15-20 questions: >= 75 % right, 0 confident wrong |
| 6 | jobs 3-6 | one at a time, each measured |

Known limits: camera 17.5 cm up (no tabletops until the pan-tilt head); hidden things need another
view; ways under ~50 cm are the nav work; dark rooms; glass and very dark/shiny things.

## 5. Status (2026-11-21 morning)

### Done -- tested, live

| part | where | commit | measured |
|---|---|---|---|
| Keep every keyframe: photo + aligned depth + camera pose AT THE STAMP + map <- odom + room; moved 0.40 m / turned 20 deg / scene changed / 30 s still | Jetson `perception/nodes/frame_capture.py`, `frame_store.py` (`./rover vlm` starts it) | rover 4a82fdc | depth matched at 0 ms; a helmet put in view saved within ~2 s; no false saves in 2 min still; 8 % of a core |
| Send frames to the Mac (they wait on disk when it is off) | Jetson `perception/nodes/frame_uploader.py` | rover 3bb4570 | 97 frames overnight, all sent |
| Memory: EmbeddingGemma 2 (768, whole + 4 quarters), search one frame per place, the newest of a place | Mac `kino_memory.py` :8094 (`kmem`) | Pi a9be328 | 3.0 s per frame on the CPU; search 35-50 ms; Gemma 4 unchanged |
| Find chain: search -> OWLv2 (Mac CPU) -> EG2 crop check -> Gemma close-up only when unsure (own slot 4); newest sighting first; honest near miss ("maybe ...") | Mac `kino_find.py` :8095 `/find` | Pi 509d520, 0f020d6, 6b15387 | tour: 14/17 found, every box right, absent 8/8 refused |
| The same check on a live photo (the robot's fresh look) | Mac `/find_in` | Pi 6b15387 | 36 labelled photos: 15/20 found, every box right, absent 16/16 refused (old Gemma-only look: 11/16 made up) |
| Position from ANY remembered frame (its own depth, K, pose; earlier power-on through the house map) | Jetson `pixel_to_goal.py` | rover 72373af | a 13 min old frame: helmet / bottle placed; owner: positions "mostly correct" (one live check) |
| "go near X": frame memory first, then this session's photos, object memory, search; every look uses the checked chain; Mac down -> the old ways | Pi `tools/frame_memory.py`, `tools/approach.py` | Pi 00dfd54, 6b15387 | live: "go near the helmet" -> memory 9.5 s, looked, drove (0.5 cm), arrival saw it |
| "where is X?" without moving (`last_seen` tool, all three agents, prompts) | Pi `tools/frame_memory.py`, `tools/__init__.py`, `prompts.py` | **NOT COMMITTED** (owner said stop) | live: helmet / dustbin / bottle answered; red carpet -> "not sure, the closest was a brown cardboard box"; tests 783/783 |

### Left

| # | item | why | needs |
|---|---|---|---|
| 1 | Commit `last_seen` | only in the Pi working copy | owner's go |
| 2 | Keep the distance in spoken answers; "my right" (the robot's) not "your right" | the brain rewords "1.4 m, ahead to my left" into "just ahead and to your right" | prompt change + live check |
| 3 | Tape-measured position test: 5 objects, seen from 3 spots | the cm accuracy is proven only by one "mostly correct" | **left-back motor fixed**, owner + tape |
| 4 | "go near the dustbin" again | the checked fresh look on the robot; the last run stopped on wheel stalls | **left-back motor fixed** |
| 5 | "not seen" is slow (up to ~40 s) | it checks 6 frames + up to 4 Gemma close-ups before giving up | small: earlier stop |
| 6 | Frames saved while the robot was LIFTED | its pose is wrong then -> wrong positions later | small: skip frames when the wheels/IMU say lifted |
| 7 | One object from many sightings (average the positions, say "it moved") | accuracy, and "it was here at 6, it's gone now" | medium (object memory, job 3) |
| 8 | "This is MY water bottle" -- remember by its look | Gemma never calls the owner's orange bottle a "water bottle" | medium (job 6) |
| 9 | Room names ("go to the kitchen") | rooms by name on the house map | a house tour (job 5) |
| 10 | What changed (job 4); facts by EG2 instead of bge-small (job 7) | memory stays current; one model | later |
| 11 | SigLIP 2 vs EG2 again on a bigger tour | tied 15/17 on one room | a multi-room tour |

Hardware: the **left-back motor** -- the nudge gave L 1.3 / R 2.6 cm/s for 5.0 commanded and drives stalled
("turn stuck", "encoders read 0"). Owner checking.
