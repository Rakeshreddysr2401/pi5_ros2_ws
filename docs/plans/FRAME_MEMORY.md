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
