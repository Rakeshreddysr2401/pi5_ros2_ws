#!/usr/bin/env python3
"""Talk, and see what Apple's recogniser (the Mac, kino-stt) writes -- nothing in between.

    ./scripts/voice_lab/mac_stt_live.py               # robot mic -> Mac, en-IN
    ./scripts/voice_lab/mac_stt_live.py --lang en-US
    ./scripts/voice_lab/mac_stt_live.py --context Kino      # give Apple the name as a hint
    ./scripts/voice_lab/mac_stt_live.py --parakeet          # + the Pi's own recogniser, same audio

No wake word, no noise gate, no resampling: the mic's native stream
(PipeWire's default source, e.g. 48 kHz / 24-bit / stereo) is cut into
sentences and each one is sent to the Mac as those exact bytes in a WAV.
The robot's own speech detector (Silero VAD, on a 16 kHz copy) only decides
where a sentence starts and ends -- it never changes the audio. A loudness
threshold did that at first and cut speech into syllables (2026-10-20: the
AM-C28's room level is 0.03-0.06 and normal speech only 0.10-0.19). Every sentence is saved with its transcript under
~/wake_lab/mac_live/ (transcripts.tsv) to listen back: aplay <file>.

The robot keeps listening at the same time (PipeWire shares the mic); it
answers only if you say its name.
"""
import argparse
import collections
import io
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import wave

import numpy as np
import requests

MAC_URL = os.environ.get("MAC_STT_URL", "http://singireddys-mac-mini.local:8091/stt")
OUT_DIR = os.path.expanduser("~/wake_lab/mac_live")
FRAME_S = 0.03          # the VAD judges 30 ms at a time
VAD_MODEL = os.path.expanduser("~/ros2_ws/src/langrobo_ros/models/vad/silero_vad.onnx")
PRE_ROLL_S = 0.4        # keep this much before speech starts (the first syllable)
END_SILENCE_S = 0.8     # this much quiet ends a sentence
MIN_SPEECH_S = 0.3      # shorter blips are not sent
MAX_S = 15.0
FORMATS = {"s16le": (2, np.int16), "s24le": (3, None), "s32le": (4, np.int32)}


def default_source() -> tuple[str, str, int, int]:
    """(name, format, rate, channels) of PipeWire's default mic, as it runs natively."""
    name = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True).stdout.strip()
    for line in subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True).stdout.splitlines():
        cols = line.split("\t")
        if len(cols) >= 4 and cols[1] == name:
            fmt, ch, rate = cols[3].split()[:3]
            return name, fmt, int(rate.rstrip("Hz")), int(ch.rstrip("ch"))
    sys.exit(f"cannot find the default source {name!r} in pactl")


def to_mono_float(raw: bytes, fmt: str, channels: int) -> np.ndarray:
    """A float copy for measuring loudness (and for --parakeet); the raw bytes are what is sent."""
    width = FORMATS[fmt][0]
    if fmt == "s24le":
        b = np.frombuffer(raw[: len(raw) // 3 * 3], dtype=np.uint8).reshape(-1, 3)
        x = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        x = np.where(x & 0x800000, x - (1 << 24), x).astype(np.float32) / (1 << 23)
    else:
        x = np.frombuffer(raw, dtype=FORMATS[fmt][1]).astype(np.float32) / float(1 << (8 * width - 1))
    return x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1)


def vad_frame(mono: np.ndarray, rate: int) -> bytes:
    """A 16 kHz int16 copy of one frame, for the VAD only."""
    n = int(round(len(mono) * 16000 / rate))
    x = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def quality(mono: np.ndarray, rate: int) -> str:
    """How clear the sentence is: the share of sound above 2 kHz (where s/t/k/h
    live -- the AM-C28 gave ~5 %, 2026-10-20) and speech over background in dB."""
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / rate)
    voice = spec[(freqs >= 80) & (freqs < 8000)].sum() + 1e-12
    clear = 100 * spec[(freqs >= 2000) & (freqs < 8000)].sum() / voice
    hop = int(rate * FRAME_S)
    lv = np.sqrt((mono[: len(mono) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1)) + 1e-9
    snr = 20 * np.log10(np.percentile(lv, 90) / np.percentile(lv, 10))
    verdict = "good" if clear >= 15 and snr >= 20 else "ok" if clear >= 8 and snr >= 15 else "poor"
    return f"{verdict:<4}  clarity {clear:4.1f} % (want >15)   voice over noise {snr:4.1f} dB (want >20)"


def wav_bytes(raw: bytes, fmt: str, rate: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(FORMATS[fmt][0])
        w.setframerate(rate)
        w.writeframes(raw)
    return buf.getvalue()


class Parakeet:
    """The Pi's live recogniser on a 16 kHz mono copy (what stt_node would hear)."""

    def __init__(self):
        sys.path.insert(0, os.path.expanduser("~/ros2_ws/src/pi5_voice_pkg"))
        from pi5_voice_pkg.stt_providers.local_sherpa import LocalSherpaProvider
        self._p = LocalSherpaProvider(os.path.expanduser(
            "~/ros2_ws/src/langrobo_ros/models/sherpa/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"), threads=2)

    def transcribe(self, mono: np.ndarray, rate: int) -> str:
        n = int(len(mono) * 16000 / rate)
        x = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype(np.float32)
        return self._p.transcribe(x, 16000).strip()


def send(n: int, raw: bytes, args, spec, parakeet, log, lock):
    fmt, rate, channels = spec
    wav = wav_bytes(raw, fmt, rate, channels)
    secs = len(raw) / (FORMATS[fmt][0] * channels * rate)
    stamp = time.strftime("%H:%M:%S")
    name = time.strftime("%Y%m%d-%H%M%S") + f"-{n:03d}.wav"
    with open(os.path.join(OUT_DIR, name), "wb") as f:
        f.write(wav)
    q = {"lang": args.lang}
    if args.context:
        q["context"] = args.context
    t = time.monotonic()
    try:
        r = requests.post(f"{MAC_URL}?{urllib.parse.urlencode(q)}", data=wav,
                          headers={"Content-Type": "audio/wav"}, timeout=20)
        body = r.json()
        text = body.get("transcript", "") if r.ok else f"<error {r.status_code}: {body}>"
        mac_s = body.get("secs")
    except Exception as e:  # noqa: BLE001
        text, mac_s = f"<mac unreachable: {e}>", None
    total_ms = (time.monotonic() - t) * 1000
    with lock:
        print(f"\n#{n}  {stamp}  ({secs:.1f} s of audio, {len(wav) / 1e6:.1f} MB)")
        print(f"   apple    : {text if text else '(nothing recognised)'}")
        print(f"   audio    : {quality(to_mono_float(raw, fmt, channels), rate)}")
        print(f"   time     : {total_ms:.0f} ms round trip" + (f" (Apple itself {mac_s * 1000:.0f} ms)" if mac_s else ""))
        if parakeet:
            t = time.monotonic()
            pk = parakeet.transcribe(to_mono_float(raw, fmt, channels), rate)
            print(f"   parakeet : {pk if pk else '(nothing recognised)'}   ({(time.monotonic() - t) * 1000:.0f} ms)")
        else:
            pk = ""
        print(f"   saved    : {os.path.join(OUT_DIR, name)}", flush=True)
        log.write(f"{stamp}\t{name}\t{secs:.2f}\t{args.lang}\t{text}\t{pk}\n")
        log.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lang", default="en-IN", help="en-IN (default) or en-US: both run on the Mac itself")
    ap.add_argument("--context", default="", help='hint words for Apple, e.g. "Kino,Hey Kino" (default: none)')
    ap.add_argument("--parakeet", action="store_true", help="also show the Pi's recogniser on the same sentence")
    ap.add_argument("--vad", type=float, default=0.5, help="speech-detector threshold 0-1 (default 0.5, as the robot)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    source, fmt, rate, channels = default_source()
    if fmt not in FORMATS:
        sys.exit(f"mic format {fmt} not handled")
    try:
        health = requests.get(MAC_URL.rsplit("/", 1)[0] + "/health", timeout=4).json()
        if health.get("auth") != "authorized":
            sys.exit(f"Mac kino-stt not authorized: {health}")
    except requests.RequestException as e:
        sys.exit(f"Mac kino-stt not answering at {MAC_URL} ({e})")
    parakeet = Parakeet() if args.parakeet else None
    sys.path.insert(0, os.path.expanduser("~/ros2_ws/src/pi5_voice_pkg"))
    from pi5_voice_pkg.vad_silero import SileroVad
    vad = SileroVad(VAD_MODEL, threshold=args.vad, end_threshold=min(0.35, args.vad))

    width = FORMATS[fmt][0] * channels
    frame_bytes = int(rate * FRAME_S) * width
    print(f"mic: {source}\n     {rate} Hz, {FORMATS[fmt][0] * 8}-bit, {channels} ch -- sent to the Mac exactly as recorded")
    print(f"mac: {MAC_URL}  lang={args.lang}  context={args.context or '(none)'}")
    print("speak now (Ctrl-C to stop)", flush=True)

    rec = subprocess.Popen(["parec", f"--device={source}", f"--format={fmt}", f"--rate={rate}",
                            f"--channels={channels}", "--raw", "--latency-msec=30"],
                           stdout=subprocess.PIPE, bufsize=0)
    pre = collections.deque(maxlen=int(PRE_ROLL_S / FRAME_S))
    speech, quiet, n = [], 0, 0
    lock = threading.Lock()
    log = open(os.path.join(OUT_DIR, "transcripts.tsv"), "a", encoding="utf-8")
    try:
        while True:
            raw = rec.stdout.read(frame_bytes)
            if not raw:
                sys.exit("mic stream ended")
            loud = vad.is_speech(vad_frame(to_mono_float(raw, fmt, channels), rate))
            if not speech:
                pre.append(raw)
                if loud:
                    speech, quiet = list(pre), 0
                    pre.clear()
                    print("  (hearing you...)", end="\r", flush=True)
                continue
            speech.append(raw)
            quiet = 0 if loud else quiet + 1
            dur = len(speech) * FRAME_S
            if quiet * FRAME_S >= END_SILENCE_S or dur >= MAX_S:
                voiced = dur - quiet * FRAME_S
                if voiced >= MIN_SPEECH_S:
                    n += 1
                    threading.Thread(target=send, daemon=True,
                                     args=(n, b"".join(speech), args, (fmt, rate, channels), parakeet, log, lock)).start()
                speech = []
    except KeyboardInterrupt:
        print(f"\nstopped. {n} sentences in {OUT_DIR}/transcripts.tsv")
    finally:
        rec.terminate()


if __name__ == "__main__":
    main()
