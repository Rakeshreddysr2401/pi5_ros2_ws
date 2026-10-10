import os, sys, time, numpy as np, soundfile as sf, torch
L = '/lab'; sys.path.insert(0, f'{L}/models/indic-transcribe-core')
from indic_transcribe import IndicTranscribe
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from IndicTransToolkit.processor import IndicProcessor
asr = IndicTranscribe.from_pretrained(f'{L}/models/indic-transcribe-core', device='cuda')
MT = f'{L}/models/indictrans2-indic-en-dist-200M'
tok = AutoTokenizer.from_pretrained(MT, trust_remote_code=True)
mt = AutoModelForSeq2SeqLM.from_pretrained(MT, trust_remote_code=True, dtype=torch.float16).to('cuda').eval()
ip = IndicProcessor(inference=True)
def tr(t, beams):
    b = tok(ip.preprocess_batch([t], src_lang='tel_Telu', tgt_lang='eng_Latn'), return_tensors='pt', padding='longest').to('cuda')
    with torch.no_grad(): o = mt.generate(**b, num_beams=beams, max_length=128, use_cache=False)
    return ip.postprocess_batch(tok.batch_decode(o, skip_special_tokens=True), lang='eng_Latn')[0]
f = sorted(os.listdir(f'{L}/data/test'))[:10]
x0, _ = sf.read(f'{L}/data/test/{f[0]}', dtype='float32'); asr(x0[:48000], lang='te'); tr('ఆగు', 1)
for sec in (2, 3, 5):
    ts = []
    for n in f:
        x, sr = sf.read(f'{L}/data/test/{n}', dtype='float32'); x = x[:sr*sec]
        torch.cuda.synchronize(); t = time.time(); asr(x, lang='te'); torch.cuda.synchronize(); ts.append(time.time() - t)
    print(f'ASR {sec}s clip: {np.median(ts):.2f}s median')
CMDS = ['మిత్ర, వంటగదికి వెళ్ళు', 'నీకు ఏమి కనిపిస్తోంది?', 'ఆగు', 'ఎర్ర సీసా దగ్గరికి వెళ్ళు', 'ఇప్పుడు సమయం ఎంత?',
        'ఎడమ వైపు తిరుగు', 'ఒక మీటర్ ముందుకు వెళ్ళు', 'నా వెంట రా', 'ఒక పాట పెట్టు', 'సౌండ్ పెంచు',
        'షాపింగ్ లిస్ట్‌లో పాలు చేర్చు', 'టేబుల్ మీద ఏమైనా ఉందా?', 'మిత్ర, కుర్చీ ఎంత దూరంలో ఉంది?']
for beams in (1, 5):
    ts = []
    for c in CMDS:
        t = time.time(); e = tr(c, beams); ts.append(time.time() - t)
        if beams == 5: print(f'  {c}  ->  {e}')
    print(f'MT beams={beams}: {np.median(ts):.2f}s median per command')
