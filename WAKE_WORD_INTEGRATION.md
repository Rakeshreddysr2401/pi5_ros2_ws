# Wake word "Mitra" — train it on the laptop, plug it into the robot

**Written 2026-09-20. Status: the robot has NO custom wake model yet** — it
runs a borrowed "hey jarvis" model, and the wake gate is switched OFF, so it
transcribes everything it hears. This file is the complete recipe: what you
produce on the laptop, how, and exactly where each piece goes in this repo.
Do the parts in order; each ends with a check.

Plain-language summary of the whole thing: a wake-word model is a tiny
neural net that listens to the mic all the time and outputs a number 0–1 for
"did I just hear *Mitra*?". We train it from (a) hundreds of synthetic
recordings of the word in many voices, and (b) a few dozen recordings of
**you** saying it, then tell the robot to use it.

---

## What you will end up with

| File | What it is | Where it goes in this repo |
|---|---|---|
| `mitra.onnx` | the wake-word model (~0.3–1 MB) | `src/langrobo_ros/models/wake/mitra.onnx` — **tracked in git** (the `.gitignore` has an exception for `models/wake/`) |
| `mitra_verifier.pkl` | *optional* "personal verifier": a small classifier trained on YOUR voice, run only when the base model already thinks it heard the word. Cuts false wakes from other people/TV. | `src/langrobo_ros/models/wake/mitra_verifier.pkl` — tracked |
| your clips | `mitra_pos/` (you saying "Mitra") and `mitra_neg/` (you saying other things) | keep them outside the repo (they are personal audio); back them up — you will retrain when the mic changes |

How the robot uses them (already implemented — nothing to code):

- `pi5_voice_pkg/wake/openwakeword_detector.py` loads `wake_model_path`
  (an `.onnx`) and, if set, `wake_verifier_path` (the `.pkl`), keyed by the
  model's file stem — so the base model **must be named `mitra.onnx`** for
  the verifier to attach.
- `stt_node` feeds it every 30 ms frame while asleep, fires when the score
  ≥ `wake_threshold`, then listens for `follow_up_window_s`. It transcribes
  nothing until then (cost + privacy: nothing goes to Sarvam while asleep).
- Everything is switched by `src/pi5_voice_pkg/config/voice_params.yaml`
  (Part F).

---

## Part A — Laptop setup (once)

openWakeWord's training pipeline wants Linux (or WSL) and Python 3.9–3.11.
The **official training notebook is the supported path** — everything below
is that notebook, run on your laptop (or in Google Colab if the laptop is
slow: the notebook is built for Colab, and a free GPU finishes in ~30 min).

```bash
# 1. a clean virtualenv
python3.11 -m venv ~/oww && source ~/oww/bin/activate
pip install --upgrade pip

# 2. openWakeWord with training extras, plus what the helper scripts need
pip install openwakeword numpy sounddevice jupyter
git clone https://github.com/dscripka/openWakeWord ~/openWakeWord
cd ~/openWakeWord && pip install -e .[full]      # training deps (torch etc.)

# 3. the bundled feature models the trainer + the robot both use
python3 -c "import openwakeword.utils as u; u.download_models()"
```

**On a Mac (Apple Silicon — M1 or the M4 Mini):** same steps, with
`brew install python@3.11` first and `pip install -e ".[full]"` quoted (zsh).
Torch uses the Apple GPU (MPS) on its own. The M4 Mini is also the robot's
LLM server — train when you are not talking to the robot. If the
synthetic-clip generator (`piper-sample-generator`) fails to install on
macOS, do not fight it: run **Part B only** in Google Colab (the notebook is
built for it, free GPU, ~30 min), download `mitra.onnx`, and do Parts C–E on
the Mac.

The notebook: `~/openWakeWord/notebooks/automatic_model_training.ipynb`
(there is also a `*_simple` variant with fewer knobs — start with that).
Open with `jupyter notebook`, or upload to Colab.

**Check:** `python3 -c "import openwakeword, torch; print('ok')"` prints ok.

---

## Part B — Synthetic data + training (the notebook)

The notebook does four things, and you only change the first:

1. **Target phrase.** Set it to the word as you want it *heard*. Text-to-
   speech voices pronounce spellings, so give the spelling that sounds right
   when an English TTS says it. Try in this order and keep the one that
   sounds like how your household says it:
   - `mitra`  (most TTS voices: "MIT-ruh" — usually right)
   - `mithra` (if the first comes out as "MY-tra")
   - a list works too, e.g. `["mitra", "hey mitra"]` so both forms wake it.

   The notebook also takes **adversarial/negative phrases** — words that
   sound close and must NOT wake it. Give it: `mithun, meter, mitali, mitten,
   metro, nitro`. This is what stops "meter" waking the robot.
2. **Generate samples.** It synthesises thousands of clips of the phrase
   with the piper TTS voices, at random speeds/pitches, and mixes in room
   echoes and background noise. Defaults are fine; if you have time, raise
   the sample count (more = better, ~1 h on CPU).
3. **Train.** Uses pre-computed features of many hours of "not the wake
   word" speech that the notebook downloads (a few GB, once). Defaults are
   fine.
4. **Export.** You get `mitra.onnx` (and a `.tflite` you can ignore).

**Do not skip the adversarial phrases** — the difference between a model
that wakes on "Mitra" and one that also wakes on "meter" is exactly that
list.

**Check:** the notebook's own test cell reports accuracy/false-positive
rate; anything above ~0.9 recall with a low false-positive rate is a good
first model. Fine-tuning comes from Parts C–E, not from re-running this.

---

## Part C — Your own voice (laptop mic) — ~10 minutes

Synthetic voices never sound exactly like you, and the model has never heard
your room. These clips do two jobs: they let you *measure* the model on real
speech (Part D), and they train the personal verifier (Part E).

```bash
cd ~/ros2_ws              # or wherever this repo is checked out on the laptop
source ~/oww/bin/activate

# 30 clips of just the word. Vary: normal, soft, from across the room, fast,
# with the TV on. It counts down and says GO; say "Mitra" once.
python3 scripts/wake_record_clips.py positive --out ~/wake_data/mitra_pos --n 30

# 20 clips of anything ELSE: similar-sounding names ("Mithun", "meter"),
# ordinary sentences, someone else in the house talking.
python3 scripts/wake_record_clips.py negative --out ~/wake_data/mitra_neg --n 20
```

Tips: hold the laptop where the robot's speaker will be relative to you
(1–3 m, not at your mouth). Get one or two other household members to
record a few positives too — the robot should answer them as well. If a
clip is too quiet the script says so and skips it.

**Check:** `ls ~/wake_data/mitra_pos | wc -l` ≥ 25.

---

## Part D — Test the model on the laptop BEFORE the robot

```bash
python3 scripts/wake_score.py path/to/mitra.onnx ~/wake_data/mitra_pos ~/wake_data/mitra_neg
```

It prints the peak score per clip and a summary. What you want:
- `fired on 27/30` or better of your wake-word clips at threshold 0.5;
- `false fires on 0/20` of the other clips;
- the line "a safe threshold sits between X and Y" — remember Y, it is your
  starting `wake_threshold` on the robot.

If positives score low (median under ~0.4): the TTS spelling did not match
how you say it — go back to Part B with the other spelling. If negatives
fire: add those exact words to the adversarial list and retrain.

---

## Part E — Personal verifier (optional, recommended for a home with TV)

Trains a small classifier on *your* clips, applied only to frames the base
model already scores above `wake_verifier_threshold`. It does not make the
model hear you better; it makes it ignore other voices/TV saying similar
things.

```bash
python3 - <<'EOF'
import glob
from openwakeword.custom_verifier_model import train_custom_verifier
train_custom_verifier(
    positive_reference_clips=sorted(glob.glob("/home/YOU/wake_data/mitra_pos/*.wav")),
    negative_reference_clips=sorted(glob.glob("/home/YOU/wake_data/mitra_neg/*.wav")),
    output_path="/home/YOU/wake_data/mitra_verifier.pkl",
    model_name="/path/to/mitra.onnx",
)
EOF
# re-score WITH the verifier; positives should still fire, negatives less
python3 scripts/wake_score.py mitra.onnx ~/wake_data/mitra_pos ~/wake_data/mitra_neg \
        --verifier ~/wake_data/mitra_verifier.pkl
```

(The function takes **lists of file paths**, despite its docstring saying
"directory".) If the verifier makes your own positives stop firing, skip it
— a verifier trained on 30 clips of one person can be too narrow; record
more positives from everyone who should be able to wake the robot.

---

## Part F — Put it in the repo and switch the robot to it

1. **Copy the files** (from the laptop, over ssh):
   ```bash
   scp mitra.onnx            rakhi24@rakhi24-desktop.local:~/ros2_ws/src/langrobo_ros/models/wake/
   scp mitra_verifier.pkl    rakhi24@rakhi24-desktop.local:~/ros2_ws/src/langrobo_ros/models/wake/   # if made
   ```
   Keep the name `mitra.onnx` — the verifier is keyed by that stem.

2. **Edit `src/pi5_voice_pkg/config/voice_params.yaml`**, `pi5_stt_node`
   section — these five lines are the whole switch:
   ```yaml
   wake_detector: openwakeword          # was transcript_alias (gate OFF)
   wake_model_path: /home/rakhi24/ros2_ws/src/langrobo_ros/models/wake/mitra.onnx
   wake_threshold: 0.5                  # start at the value Part D suggested
   wake_verifier_path: /home/rakhi24/ros2_ws/src/langrobo_ros/models/wake/mitra_verifier.pkl   # or "" without one
   require_wake: true                   # only matters in transcript_alias mode, but set it: nothing forwards without the name
   ```
   `wake_word: hey_jarvis` can stay — `wake_model_path` wins when set.
   `wake_aliases: ["mitra", "hey mitra"]` are already there (the transcript
   fallback if the model ever fails to load).

3. **Restart voice and read the log:**
   ```bash
   systemctl --user restart langrobo-voice
   journalctl --user -u langrobo-voice -f -o cat | grep -E "wake|diag|command"
   ```
   You must see `wake_detector = openwakeword ('.../mitra.onnx')`. If you see
   `transcript_alias` instead, the model failed to load and the line just
   before says why (path, bad file) — it degraded on purpose, fix and
   restart.

4. **Tune the threshold on the real mic.** While asleep the node logs
   `[diag] asleep peak_wake_score=0.xx` every 10 s (`diag_log_period_s`).
   Say "Mitra" a few times from where you normally stand and watch the peak;
   stay quiet and watch the floor. Set `wake_threshold` clearly below your
   peaks and above the floor (the "hey jarvis" stand-in needed 0.35 through
   the Stone's mic — expect something similar; the Stone's 8 kHz mic is
   narrower than your laptop's). Restart after each change.

5. **Commit:**
   ```bash
   git add src/langrobo_ros/models/wake/mitra.onnx src/langrobo_ros/models/wake/mitra_verifier.pkl \
           src/pi5_voice_pkg/config/voice_params.yaml
   git commit -m "Mitra wake word: trained model + gate on"
   ```

---

## Part G — Exit test (from VOICE_ROADMAP.md Phase 1)

Do this on the robot, with the Stone, in the real room:

| Test | Pass |
|---|---|
| Say "Mitra" 20 times at conversational volume from 2 m | ≥ 18 wake (`wake word ... detected — listening` in the log) |
| 30 min of TV / conversation without the name | 0 wakes, and `ros2 topic echo /voice/stt_meta` shows **nothing** (no transcription at all) |
| "Mitra, what time is it" | answered; then "and the date" **without** the name, inside `follow_up_window_s` → answered |
| Someone says "meter" / "Mithun" | no wake |

Record the numbers in VOICE_ROADMAP.md Phase 1 and tick the boxes. After
this, the "haan boss?" acknowledgement cue (Phase 1, same file) is the next
piece.

---

## Troubleshooting

- **Fires on silence / noise:** threshold too low, or the Stone's mic gain
  (`bt_mic_gain: 4.0` in `pi5_audio_device`) is amplifying hiss — check the
  idle `peak_wake_score` floor first; lower gain slightly if the floor is
  above 0.1.
- **Never fires but scored well on the laptop:** the Stone is 8 kHz
  narrowband (CVSD) and the laptop mic is wideband; record 20 positives
  **through the Stone** (`python3 scripts/wake_record_clips.py positive
  --device pipewire --out ~/wake_data/mitra_pos_stone` on the Pi, with
  `langrobo-voice` stopped so the mic is free) and retrain the verifier /
  add them to training.
- **Wakes on other people's "Mitra" when you don't want it to:** that is
  what the verifier is for (Part E). If you DO want everyone to wake it,
  don't use one.
- **Different speaker/mic later (Buds, a new speaker):** re-run Part F step
  4 — the threshold is per mic. The model itself does not change.
- **Model loads but `wake_verifier_path` errors:** the base file is not
  named `mitra.onnx`, or the `.pkl` was trained against a different
  `mitra.onnx` — retrain the verifier (Part E) against the exact model you
  copied.
