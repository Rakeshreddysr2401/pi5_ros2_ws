# Voice, Telegram and navigation fixes — 2026-11-07

Branch in both repos: `fix/voice-telegram-navigation-2026-11-07`.
Jetson evidence: `~/rover/docs/VOICE_NAV_AUDIT_2026-11-07.md`.

## Changes

| Observed problem | Change | Verification |
|---|---|---|
| A startup forward request silently used timed driving while exact movement was unavailable | Real ROS bridge returns `not_ready`; it cannot select the stub's timed fallback. Stop remains available. | Missing-listener tests cover forward/back/left/right without a published drive, plus a working stop. |
| "I will try to move back again" was spoken without a tool call | A movement-owning agent must call a tool or clarify before it has a tool result this turn. Unsupported text cannot stream ahead of validation. Previous turns and handovers are not evidence. | Regression includes the actual refused-reverse follow-up, cloud fallback, silence and offline messages. |
| "Come little back" requested 50 cm | Prefer 10 cm for a vague nudge; asking for a precise amount is also allowed. Explicit distances are preserved. Straight movement without an amount asks how far. | Real Gemma: small backward/nudge 10 cm, explicit 50/20 cm preserved, unspecified forward/back clarified. "Forward a bit" still asks how far, an accepted conservative outcome. |
| "Me", "Started", "call the door" were interpreted as actions | Clarification takes priority in the shared identity and navigation's first decision. | Final real-model navigation checks clarify all three, about 1–1.2 s warm. This is measured prompt behavior, not a universal intent guarantee. |
| An unclear fragment caused a long blank reply and retry | First navigation response is non-streaming and bans the thinking opener. Closing-marker residue is cleaned before validation. | Repeated thought markers reproduced during the audit; first-call configuration is pinned in regression tests; final real-model movement/handover cases pass with this configuration. |
| Repeated speech after tool announcements | Background-search acknowledgement is marked already announced; muted non-streaming responses and retries remain muted. Later tool steps still speak. Fractional distances no longer get truncated at the decimal point. | Speech-stream and ROS announcement tests, including handover/second-step speech and fractional distances. |
| Slow search kept the voice turn occupied for eight seconds before detaching | Default quick-result window is 0.5 s (`LANGROBO_APPROACH_QUICK_S` remains available). | Slow-search test uses the production default and returns within 2 s; later result, requester, errands, cancellation and single-goal tests pass. Underlying VLM/navigation work is unchanged. |
| Sarvam balance exhausted, actual provider hard to inspect | `/status` includes last observed STT/TTS configured/actual providers, fallback reason, success and observation age. | Status tests distinguish unknown state and working Piper fallback with HTTP 402. |
| Fleet status falsely showed visual odometry down | Recognize Isaac's `vslam_container` as well as the old `vo_node`. | Live status lists every Jetson layer up. |
| Four-second `/slots` timeout prescribed restarting a working Mac | Bounded read retry, list validation, and a diagnostic that distinguishes unreadable status from insufficient slots. | Reproduced a nearly four-second slot read while inference was active; full fleet check subsequently ALL OK with five slots. |

Other agents and replies after tool results retain streaming. No ROS topic,
speech delimiter, KV slot, image-history projection, STT model, microphone
processing, Nav2/RL policy, or obstacle threshold was changed.

## Validation

- Core: **722 passed** (`cd src/langrobo_core && python3 -m pytest tests/ -q`).
- Voice: **144 passed** (`cd src/pi5_voice_pkg && python3 -m pytest tests/ -q`).
- ROS bridge: **30 passed** (source `scripts/ros_env.sh`, then `cd src/langrobo_ros && python3 -m pytest test/ -q`).
- Real Gemma, spare slot 4, no tools executed: the 30 chat/vision cases passed;
  after the last navigation-only prompt edit, all **27 navigation cases passed**
  (`python3 scripts/gemma_flow_eval.py --agent navigate`). Together these cover
  the script's 57 cases. This includes multi-step drives, agent handovers,
  photo/arrival errands and follow commands. A full run before the final
  navigation clarification edit was 56/57; its remaining failure was fixed and
  is included in the 27-case rerun.
- Cold slot switches in this evaluation still cost about 20–34 s; it reuses
  one spare slot across agents. Warm navigation decisions were about 1–3.4 s.
  Do not report these as full microphone-to-wheel or microphone-to-audio times.
- `./scripts/build_ws.sh`: both ROS packages build successfully.
- `./scripts/fleet.sh check`: **ALL OK**, including five LLM slots, live
  odometry/LiDAR/wheels/camera, motion/vision/follow listeners, slam and Studio.
- Jetson offline follow tests: **24 passed**. Deployed RL policy inference and
  shield smoke checks passed without a ROS publisher or physical motion.

## Remaining limits

- Restoring Sarvam requires account credits. Piper fallback remains in use;
  code cannot replenish the balance.
- No original recordings were available to score transcription accuracy for
  the misheard-looking fragments. STT/audio was deliberately not tuned by ear.
- The reverse refusal and failed follow search have sensor-based explanations
  in the Jetson logs. They are not proven false detections. A measured real
  scene is needed before changing clearance rules.
- RL is enabled and has been used in a real recovery. Training metadata is not
  a real-floor success rate; no retraining or policy replacement was done.
- No autonomous floor regression drive was issued during this audit. Tool
  selection tests do not establish physical arrival precision or clearance.
- Long visual searches and cold-cache delays remain possible. The shorter
  background wait improves conversational availability, not the underlying
  arrival time.
