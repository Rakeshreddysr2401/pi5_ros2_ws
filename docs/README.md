# docs — the index

Start with [KINO_SYSTEM.md](../KINO_SYSTEM.md) (the whole robot on one page, the same file in all
three repos), the repo [README](../README.md) and [CLAUDE.md](../CLAUDE.md) (the project guide).
Code comments name these files without a path ("see OPERATIONS.md"): they are all in this folder.

## How it works and how to run it
| file | what |
|---|---|
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | end-to-end walkthrough: start here |
| [ARCHITECTURE_LLD.md](ARCHITECTURE_LLD.md) | every file, the turn lifecycle, where the latency goes |
| [OPERATIONS.md](OPERATIONS.md) | deploy, systemd, health API, troubleshooting |
| [NETWORKING.md](NETWORKING.md) | how the machines find each other |
| [TELEGRAM.md](TELEGRAM.md) | chat with the robot from a phone |
| [TODO.md](TODO.md) | open items |

## By topic
- `voice/`: the Pi 5 voice trio, wake word, speed and quality ([PI5_VOICE](voice/PI5_VOICE.md), [VOICE_ROADMAP](voice/VOICE_ROADMAP.md), [WAKE_WORD_INTEGRATION](voice/WAKE_WORD_INTEGRATION.md))
- `mac/`: the Mac mini's LLM/VLM and STT ([MAC_MINI_TASKS](mac/MAC_MINI_TASKS.md), [LLAMA_CACHE_ISSUE](mac/LLAMA_CACHE_ISSUE.md))
- `plans/`: why things were built and what is next ([INTEGRATION_GAPS](plans/INTEGRATION_GAPS.md), [FIND_AND_GO](plans/FIND_AND_GO.md), [FOLLOW_AND_FAST_EYES](plans/FOLLOW_AND_FAST_EYES.md), [INTENT_ROUTING_PLAN](plans/INTENT_ROUTING_PLAN.md), [KINO_2_PLAN](plans/KINO_2_PLAN.md))
- `rl/`: the learned-driver plans on the brain's side
- `reports/`: one dated test or audit each
- `learn/`: learning robotics + RL from this robot
