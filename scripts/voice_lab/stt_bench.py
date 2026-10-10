"""Score one English STT model on make_clips.py's clips: WER, "Mitra" hits, seconds a clip.
    THREADS=2 python3 stt_bench.py CLIPS_DIR MODELS_DIR fw:tiny.en
    ... moonshine-base-en-int8 | nemo-parakeet-tdt-0.6b-v2-int8   (sherpa-onnx dirs in MODELS_DIR)
Clips are mixed with the recorded room noise at 20 dB ("near") and 5 dB ("far")."""
import glob, json, os, re, sys, time, resource
import numpy as np
sys.path.insert(0, os.path.dirname(__file__)); from phrases import PHRASES
CLIPS, DL, THREADS = sys.argv[1], sys.argv[2], int(os.environ.get('THREADS', 2))
NUM = {'90': 'ninety', '1': 'one', '10': 'ten', 'metre': 'meter', 'mithra': 'mitra', 'mittra': 'mitra'}

def norm(t):
    t = re.sub(r"[^\w' ]", ' ', t.lower())
    return ' '.join(NUM.get(w, w) for w in t.split()).replace("what's", 'what is').split()

def wer(ref, hyp):
    r, h = norm(ref), norm(hyp); d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)

def make(name):
    if name.startswith('fw:'):
        from faster_whisper import WhisperModel
        m = WhisperModel(name[3:], device='cpu', compute_type='int8', cpu_threads=THREADS,
                         download_root=os.path.expanduser('~/ros2_ws/src/langrobo_ros/models/whisper'))
        return lambda pcm: ' '.join(s.text for s in m.transcribe(
            pcm, language='en', beam_size=1, hotwords='Mitra', condition_on_previous_text=False)[0])
    sys.path.insert(0, os.path.expanduser('~/ros2_ws/src/pi5_voice_pkg'))
    from pi5_voice_pkg.stt_providers.local_sherpa import LocalSherpaProvider
    p = LocalSherpaProvider(f'{DL}/sherpa-onnx-{name}', THREADS)
    return lambda pcm: p.transcribe(pcm, 16000)

name = sys.argv[3]
noise = np.load(f'{CLIPS}/room_noise.npy'); rng = np.random.default_rng(0)
t0 = time.time(); run = make(name); load_s = time.time() - t0
run(np.zeros(16000, np.float32))
res = {}
files = sorted(glob.glob(f'{CLIPS}/*_??.npy'))
for cond, snr in (('near', 20), ('far', 5)):
    E = N = names = names_tot = 0; secs = audio = 0.0; misses = []
    for f in files:
        ref = PHRASES[int(f[-6:-4])]
        x = np.concatenate([np.zeros(4800, np.float32), np.load(f), np.zeros(8000, np.float32)])
        o = rng.integers(0, len(noise) - len(x)); n = noise[o:o + len(x)]
        pcm = (x + n * np.sqrt((x ** 2).mean() / (n ** 2).mean() / 10 ** (snr / 10)))
        pcm = (pcm * 0.3 / max(1e-6, np.abs(pcm).max())).astype(np.float32)
        t = time.time(); hyp = run(pcm); secs += time.time() - t; audio += len(pcm) / 16000
        e, n_ = wer(ref, hyp); E += e; N += n_
        if 'mitra' in ref.lower():
            names_tot += 1; names += 'mitra' in norm(hyp)
        if e: misses.append(f'{os.path.basename(f)[:-4]}: {hyp.strip()!r}')
    res[cond] = dict(wer=round(E / N * 100, 1), name=f'{names}/{names_tot}',
                     s_per_clip=round(secs / len(files), 2), rtf=round(secs / audio, 3), misses=misses[:12])
res['load_s'] = round(load_s, 1); res['peak_rss_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
print(json.dumps({name: res}))
