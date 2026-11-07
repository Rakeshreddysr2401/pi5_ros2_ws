# Rover navigation v003 — overnight plan

Requested: 2026-11-07. Working deadline: **2026-11-08, 08:00 IST** unless the
owner specifies otherwise. This is a delivery target for a candidate and its
measured results, not a promise that an arbitrary real-world goal will succeed.

## Deliverable

A versioned v003 policy, the associated navigation fixes if measured necessary,
reproducible test commands, a comparison with v002, and a clear pass/fail card.
Keep v002 available for rollback. If the candidate fails the gates, deliver the
failure evidence and retain v002; do not label an inferior policy ready.

Full navigation means the existing combination: Nav2 for routes, the learned
driver for difficult local motion, goal_exec for the finish, perception and
localization supplying their inputs, and the Pi5 brain issuing requests. The
RL policy alone is not the entire navigation system.

## Verified starting point

- Laptop access works through `rakhi24@rakhi-jetson.local`, then
  `chotesh@kino.local`; direct Pi5-to-laptop authentication was rejected.
- Laptop `~/kino_sim` is clean at `9923f62`. RTX 4050 has 6 GB VRAM and was
  available when checked. Training environment: `~/miniforge3/envs/kino`.
- Training code and baseline artifacts already exist under `rl/` and
  `build/rl/v002`; the live Jetson uses `models/rl/current -> v002`.
- Read before work: laptop `CLAUDE.md`, `docs/IMPROVEMENT_LOOP.md`,
  `docs/NAV_NEXT.md` section 13, `rl/README.md`, and Jetson `RL_PLAN.md`.
- Existing data: about 54 MB of Jetson RL dumps. No owner teleoperation demos
  were found in the inspected demo directory; do not fabricate demonstrations.
- Known baseline weaknesses: corridor bends, chair gaps, late handover from
  Nav2, noisy remembered obstacles, and low objects outside sensor coverage.

## Stages and time budget

Times below are planning budgets. Log the actual start, end and outcome of
each stage. Correctness gates take precedence over using all scheduled time.

| Target window (IST) | Work | Exit requirement |
|---|---|---|
| 22:20–23:00 | Protect baselines, inventory checkpoints, verify laptop power/resources, establish comparable baseline and parity | Reproducible v002 reference; simulation/runtime observation, commands and shield agree |
| 23:00–02:00 | Implement and smoke-test the smallest measured input-model correction; train candidate(s) from v002 | Finite training, saved checkpoints, export loads through the actual Jetson policy implementation |
| 02:00–03:00 | Paired v002/v003 offline evaluation on held-out rooms, named worlds and recorded failure fixtures | Candidate meets collision/coverage/progress gates; rejected versions remain rejected |
| 03:00–06:00 | Full-stack twin tests on domain 42, with recordings and true-geometry judging | All mandatory routes/refusals measured; no touches; no silent omissions |
| 06:00–07:15 | Investigate remaining failures; keep only isolated changes that pass their regression tests; final rerun | Candidate qualification stated precisely; no untested late change |
| 07:15–08:00 | Commit, write model card/results, shut down twin and restore real-body services | Clean handoff, rollback instructions and supervised real-floor checklist |

If the offline gate fails, spend the full-stack window diagnosing that failure
or validating an isolated navigation fix. Do not promote a candidate simply
because the deadline is close.

## 1. Establish the baseline and trustworthy evaluation

1. Create separate task branches/checkouts for the laptop training work and
   actual rover changes. Record commits and checkpoint hashes. Do not replace
   `models/rl/current` or overwrite v002 evaluation files.
2. Verify that the parity checkout is current with the live rover; the laptop's
   `~/rover_dev_1101` must not silently stand in for newer Jetson code.
3. Run training/runtime parity tests before modifying perception augmentation.
4. Review the evaluator: terminal success/contact/timeout accounting, fixed
   room assignments, deterministic seeds, and skipped/unreachable cases. Fix a
   judge error before claiming policy improvement. Run v002 with that same
   evaluator, seeds, episode limits and hardware settings.
5. Preserve physical geometry as ground truth. Sensor noise and memory may
   affect what the policy/shield sees, never erase obstacles from the contact
   judge. Include genuinely impossible goals as required refusals, not as
   missing successful episodes.

## 2. Candidate training, one measured change at a time

Priority follows laptop `docs/NAV_NEXT.md` section 13:

1. Match the guide used by the current robot (LiDAR obstacle selection, 2 cm
   grid and the actual configured clearance). Measure GPU/CPU costs before
   increasing bank size or resolution.
2. Introduce realistic perceived-obstacle perturbations without changing true
   geometry: camera-edge thickness, remembered side/rear obstacles and the
   camera's limited view/range. Use documented measured ranges where available;
   label assumptions and test sensitivity elsewhere.
3. Replay the recorded real failures as fixed diagnostic cases and, where
   reconstruction is valid, training cases. Split train and held-out failures
   before training; never report a training fixture as an unseen test.
4. Keep the runtime shield and command caps in the training loop. Retain the
   existing motion/battery/lag variation. Change rewards only if rollout
   evidence identifies a reward problem; avoid simultaneous unrelated tuning.
5. Resume from a verified v002 checkpoint. Run a short smoke training job,
   test export/inference/parity, then run a bounded longer job with periodic
   checkpoints. Limit work to available GPU memory; no training while Isaac
   renders on the same 6 GB GPU.

Imitation learning from owner demos is a later addition if genuine demos
become available. It is not a prerequisite invented for this overnight run.

## 3. Offline gates

- Held-out evaluation: at least 2,000 random episodes in aggregate, with
  per-generator results and counts; reach target at least 90% on declared
  feasible cases, with **zero contacts**. Publish every excluded case and why.
- Impossible-goal tests: at least 95% end in a correct refusal/stop, with no
  contact. Never reward "success" at an unreachable target.
- Named-world tests: compare v002 and candidate on identical starts, goals,
  randomized motion packs and seeds. Include corridor_s, chairs44/50, door49,
  gap45/50/60, dead_end, pinned_side, stool_gap and an ordinary wide route.
- Evaluate both nominal and realistic noisy inputs. Report completion,
  contacts, elapsed time, minimum clearance, shield changes/stops and timeout
  rate. A faster result with contacts is a failure.
- Exported numpy inference must be compatible with the current Jetson
  observation/action schema and meet the 10 Hz control-loop budget there.
  Measure p50/p95 inference time; include sensor and loop timing separately.

## 4. Full navigation in the twin

Only after the offline gate, bring up the twin using the repository's launch
commands. Verify **ROS domain 42** at every manual entry point. Use the same
Jetson Nav2/reach/goal_exec code and the same Pi5 brain implementation.

The Jetson runs one body stack at a time. During full-stack tests, park/stop
the real body before starting its twin stack; keep real voice/chat available
where practical. No domain-0 goal, velocity command or real-floor drive is
part of this overnight validation.

Record nav traces, true pose, estimated pose, clearance and controller status.
Start with home_real and wide routes; then door49, chair gaps, corridor_s,
dead_end, pinned_side, clutter/apt_hard, impossible gap36 and ledge_probe.
Run at least two passes of the declared mandatory suite if time allows; report
anything not run as incomplete, never passed. Add samples of Pi5-issued goals
after motion-only qualification so perception/agent failures are distinguishable
from route/control failures.

Acceptance requires zero judged contacts, correct impossible-goal refusal,
reliable completion of feasible mandatory routes, and no unexplained route
regression greater than 20% in time. Record position and heading error; a
nearby substituted target must be labeled as such. A missed low obstacle or
incorrect VLM-picked object is not counted as an RL success.

When the twin exposes a navigation bug, change the real rover code, add the
recorded regression, and test the smallest fix. Do not make the twin easier to
manufacture a pass. Keep collision margins and stop behavior intact.

## 5. Morning handoff

- Versioned candidate and hashes, training configuration/seed/checkpoints,
  commits, raw results and model card.
- v002 comparison table; explicit qualified and unqualified cases.
- Measured inference/control timing and full-stack results.
- Existing v002 rollback and exact candidate selection command. Do not change
  the real default without qualification and the supervised floor check.
- Stop the twin after testing and restore/check the real rover services.
- Real-floor checklist, owner present: charged battery, measured door/gap and
  corner cases, three passes each, recorded clearances, stop/cancel check.

The overnight run can qualify a simulation candidate. Real-time CPU inference
can be measured on the Jetson without moving the rover. Real-world navigation
reliability still needs those supervised physical runs.
