"""Telugu speech -> Telugu text (Indic-Transcribe-core) -> English (IndicTrans2-200M), on the Orin GPU."""
import csv, json, os, re, sys, time
from collections import Counter
import torch
L = '/lab'
sys.path.insert(0, f'{L}/models/indic-transcribe-core')
def tsv(p): return {r[0]: r for r in csv.reader(open(p, encoding='utf8'), delimiter='\t', quoting=csv.QUOTE_NONE)}
te, en = tsv(f'{L}/data/te_test.tsv'), tsv(f'{L}/data/en_test.tsv')
names = set(os.listdir(f'{L}/data/test'))
items = []
for i, r in te.items():
    if r[1] in names and i in en and r[1] not in {x[0] for x in items}:
        items.append((r[1], r[3] if len(r) > 3 else r[2], en[i][2]))
def toks(t): return re.sub(r'[^\w\s]', ' ', t.lower()).split()
def wer(ref, hyp):
    r, h = toks(ref), toks(hyp); d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j-1] + 1, p + (r[i-1] != h[j-1]))
    return d[len(h)], len(r)
def chrf(hyp, ref, n=6, beta=2):
    h, r = hyp.lower(), ref.lower(); P = R = 0; k = 0
    for o in range(1, n + 1):
        hg = [h[j:j+o] for j in range(len(h)-o+1)]; rg = [r[j:j+o] for j in range(len(r)-o+1)]
        if not hg or not rg: continue
        c = sum((Counter(hg) & Counter(rg)).values()); P += c/len(hg); R += c/len(rg); k += 1
    P, R = P/max(k, 1), R/max(k, 1)
    return 0.0 if P + R == 0 else 100*(1+beta**2)*P*R/(beta**2*P+R)

dev = 'cuda'
t0 = time.time()
from indic_transcribe import IndicTranscribe
asr = IndicTranscribe.from_pretrained(f'{L}/models/indic-transcribe-core', device=dev)
print(f'asr loaded {time.time()-t0:.1f}s, gpu {torch.cuda.memory_allocated()/1e9:.2f} GB', flush=True)
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
MT = f'{L}/models/indictrans2-indic-en-dist-200M'
tok = AutoTokenizer.from_pretrained(MT, trust_remote_code=True)
mt = AutoModelForSeq2SeqLM.from_pretrained(MT, trust_remote_code=True, torch_dtype=torch.float16).to(dev).eval()
print(f'mt loaded, gpu {torch.cuda.memory_allocated()/1e9:.2f} GB', flush=True)
from IndicTransToolkit.processor import IndicProcessor
ip = IndicProcessor(inference=True)
def translate(t):
    pre = ip.preprocess_batch([t], src_lang='tel_Telu', tgt_lang='eng_Latn')
    b = tok(pre, return_tensors='pt', padding='longest', truncation=True, max_length=256).to(dev)
    with torch.no_grad():
        out = mt.generate(**b, num_beams=5, max_length=256, min_length=0, use_cache=False)
    dec = tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True)
    return ip.postprocess_batch(dec, lang='eng_Latn')[0]
asr(f'{L}/data/test/{items[0][0]}', lang='te'); translate('నమస్కారం')   # warm
E = N = 0; ta = tm = 0.0; c_ref = []; c_e2e = []
for k, (f, te_ref, en_ref) in enumerate(items):
    torch.cuda.synchronize(); t = time.time(); hyp = asr(f'{L}/data/test/{f}', lang='te'); torch.cuda.synchronize(); ta += time.time() - t
    if isinstance(hyp, (list, tuple)): hyp = hyp[0]
    hyp = getattr(hyp, 'text', hyp)
    e, n = wer(te_ref, hyp); E += e; N += n
    t = time.time(); en_hyp = translate(hyp); tm += time.time() - t
    c_e2e.append(chrf(en_hyp, en_ref)); c_ref.append(chrf(translate(te_ref), en_ref))
    if k < 5: print(f'TE  {hyp}\nREF {te_ref}\nEN  {en_hyp}\nGOLD {en_ref}\n', flush=True)
n = len(items)
print(json.dumps(dict(clips=n, te_wer=round(100*E/N, 1), asr_s=round(ta/n, 2), mt_s=round(tm/n, 2),
                      chrF_e2e=round(sum(c_e2e)/n, 1), chrF_mt_on_gold_te=round(sum(c_ref)/n, 1),
                      gpu_peak_gb=round(torch.cuda.max_memory_allocated()/1e9, 2))))
