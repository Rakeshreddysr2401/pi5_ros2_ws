# Intent routing — next phase (MiniLM entry classifier)

**Status: designed, not built.** Written 2026-09-08, the day the regex fast
path was deleted (ARCHITECTURE_LLD.md §3.1). This file exists so the latency
that removal cost gets bought back with a model instead of another lookup
table.

---

## 1. What we gave up, in numbers

| the turn | before (fast path) | today | with this plan |
|---|---|---|---|
| `"stop"`, `"forward 30"`, `"go to the kitchen"` | **0** LLM calls | 2 | **1** |
| `"what do you see?"` | 1 (frame pre-attached) | 3 (~60 s measured on the 12B) | **1** |
| general question | 1 | 1 | 1 |

The 3-call vision path is the expensive one and the one a demo hits first.

Nothing about **safety** changed and nothing here restores a safety property:
`agent_node._on_user_input` halts the wheels on every utterance before the
graph runs. This is a latency plan, not a safety plan.

## 2. Why not just put the regexes back

The old lane was fast and it was honest — `match()` returned `None` whenever it
was not certain, so it never guessed. What killed it is that its vocabulary was
hand-maintained and drifted away from the robot:

- `COCO_CLASSES` (48 strings) gated every object, but the only tool it
  dispatched to is `approach_described_object`, a **VLM** path with no COCO
  vocabulary. The YOLO tool that needed one was deleted when we found nothing
  publishes `/vision/detections_3d`. Net effect: `"go to the red bottle"` fell
  through to the slow path because of a stale word list.
- `OBJECT_ALIASES`, `_WORD_NUMBERS`, `_LEAD_FILLER` and eleven regexes all had
  to be edited by hand for every new phrasing a user tried.
- `_execute()`'s `look` intent called `point_camera` directly, bypassing the
  `PAN_TILT_ENABLED` gate.

So: keep the *never guess* property, drop the hand-kept vocabulary.

## 3. The design

**A MiniLM sentence embedder picks which agent the turn ENTERS. It does not
pick tools and it does not parse arguments.**

That boundary is the whole design, and it comes from a hard limit: an embedding
classifier gives you an intent *class*, not slot values. It can tell you
`"move forward thirty centimetres"` is movement; it cannot extract the `30`.
Zero-LLM *tool execution* therefore needs a parser (that was the regex), while
zero-LLM *routing* needs only a classifier. We build the second and let the
`navigate` agent do what it is already good at.

```
utterance
   │
   ▼
MiniLM embed  (~15 ms, onnxruntime, Pi5 CPU)
   │
   ├─ cosine vs per-agent centroids built from registry.py AgentSpec.examples
   │
   ├─ best ≥ THRESHOLD and margin over 2nd ≥ MARGIN ──▶ enter that agent
   │                                                    (local_agent: attach
   │                                                     a fresh frame first)
   └─ otherwise ─────────────────────────────────────▶ enter chat (today's
                                                        behaviour, unchanged)
```

**The training data already exists.** `AgentSpec.examples` in `registry.py`
holds five utterances per agent and is currently used only for routing prose.
Building centroids from it means adding an agent automatically teaches the
router about it — the same single-source-of-truth property the registry already
gives the graph, the handover grammar and the slot map. Expect to grow each
list to ~15-20 examples; that is prompt-adjacent copy, not a lookup table, and
it is the thing a person can actually maintain.

### Placement

`src/langrobo_core/langrobo_core/routing/` — the pure zone (no rclpy), next to
`turn_entry` and `registry` where routing already lives.

```
routing/
  __init__.py
  embedder.py    fastembed wrapper; optional import, raises RouterUnavailable
  intent.py      centroids, cosine, threshold + margin, no I/O
```

`intent.py` takes an injected embedder, so the 127-test core suite runs it with
a deterministic fake — no model download, no onnxruntime, still ~1.5 s.

### Dependency

```toml
[project.optional-dependencies]
intent = ["fastembed"]
```

Mirrors the `anthropic` / `gemini` / `ollama` extras already in
`pyproject.toml`. `fastembed` bundles the ONNX runtime, the tokenizer and the
model fetch in one package, and **onnxruntime is already a proven Pi5
dependency** — `pi5_voice_pkg` runs Kokoro TTS and openWakeWord on it (measured
RTF ~0.2, ~1 s model load, `wake/openwakeword_detector.py`).

Model: `BAAI/bge-small-en-v1.5` (384-dim, ~130 MB) or
`sentence-transformers/all-MiniLM-L6-v2`. Pick by measuring §5.

### Degradation (hard rule 5)

`fastembed` missing, model file missing, or first embed raises → log once at
WARNING, set a flag, and every turn enters `chat` exactly as it does today.
The robot must never fail to answer because a routing optimisation is absent.

## 4. Build order

1. `routing/intent.py` + centroid construction from `AgentSpec.examples`,
   with an injected embedder. Unit tests with a fake. **No agent_node changes.**
2. `routing/embedder.py` — fastembed behind an optional import, warm the model
   on a startup thread (never in the turn path).
3. A `LANGROBO_INTENT_ROUTING` env flag, default **off**. Ship it dark.
4. Wire into `agent_node._process`: replace
   `incoming_agent = "chat" if is_system else (self._sticky_agent or "chat")`
   with sticky → classifier → chat, in that precedence order. `[SYSTEM]` turns
   keep going to `chat` and are never classified.
5. Re-attach the frame pre-fetch for a `local_agent` verdict — the deleted
   block in `agent_node` is the reference implementation (git show the removal
   commit); keep its guard that it only fires when `get_frame(max_age_s=10.0)`
   actually returns a frame, so a camera outage still gets `look()`'s spoken
   error rather than a model hallucinating over an empty conversation.
6. Turn the flag on, measure, tune `THRESHOLD` / `MARGIN` on real utterances.

## 5. Open questions to settle with measurement, not opinion

- **Embed latency on the Pi 5 CPU**, competing with STT and TTS for cores.
  Budget ≤50 ms. If it is worse, the model is too big — try a smaller one
  before abandoning the approach.
- **THRESHOLD and MARGIN.** Start conservative (0.55 / 0.05) and tune toward
  precision: a wrong entry costs an extra handover, which is the same 2 calls
  we have today — so mis-routing is cheap, but never let it be *confidently*
  wrong, i.e. keep the fall-through to `chat` generous.
- ~~Should `navigate` become sticky?~~ **Done 2026-09-08**, in the same commit
  as the fast-path removal. A movement follow-up (`"now turn left"`) is now
  1 call instead of 2; a non-movement follow-up costs 2 and relies on
  `NAVIGATE_PROMPT` rule 6 to hand back. Watch for that rule failing in
  practice — the model answering a weather question from `navigate` is the
  symptom, and it is a prompt fix, not a routing one.
- **Does chat's handover latency actually dominate?** Nobody has measured a
  warm-slot `navigate` turn. `scripts/latency_replay.py` should produce that
  number *before* step 1, because if a warm handover is 800 ms the whole plan
  is not worth building.
- **Ack before slow actions.** The fast path spoke `"Coming to you."` *before*
  the VLM round-trip. `NAVIGATE_PROMPT` rule 3 currently says the opposite
  ("Don't narrate a move before making it"). For `approach_described_object`,
  which is genuinely slow, that rule should flip — pre-tool text is already
  streamed to TTS while the tool runs.

## 6. What must not come back

No regex intent matching, no COCO class list, no alias map, no spelled-out
number table. If the classifier cannot decide, the answer is `chat` — the
model decides. That is the point of this phase.
