"""Live Telugu lab server (Jetson GPU): POST a 16 kHz WAV to /te -> {te, en, asr_ms, mt_ms}.
Indic-Transcribe-core (Telugu speech -> Telugu text) + IndicTrans2 dist-200M (Telugu -> English)."""
import io, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import soundfile as sf, torch
L = '/lab'; sys.path.insert(0, f'{L}/models/indic-transcribe-core')
from indic_transcribe import IndicTranscribe
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from IndicTransToolkit.processor import IndicProcessor
asr = IndicTranscribe.from_pretrained(f'{L}/models/indic-transcribe-core', device='cuda')
MT = f'{L}/models/indictrans2-indic-en-dist-200M'
tok = AutoTokenizer.from_pretrained(MT, trust_remote_code=True)
mt = AutoModelForSeq2SeqLM.from_pretrained(MT, trust_remote_code=True, dtype=torch.float16).to('cuda').eval()
ip = IndicProcessor(inference=True)
lock = threading.Lock()

def translate(text):
    if not text.strip():
        return ''
    b = tok(ip.preprocess_batch([text], src_lang='tel_Telu', tgt_lang='eng_Latn'),
            return_tensors='pt', padding='longest', truncation=True, max_length=256).to('cuda')
    with torch.no_grad():
        out = mt.generate(**b, num_beams=5, max_length=256, use_cache=False)
    return ip.postprocess_batch(tok.batch_decode(out, skip_special_tokens=True), lang='eng_Latn')[0]

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        wav, sr = sf.read(io.BytesIO(self.rfile.read(int(self.headers['Content-Length']))), dtype='float32')
        if wav.ndim > 1:
            wav = wav.mean(1)
        with lock:
            t = time.time(); te = asr(wav, lang='te'); a = time.time() - t
            t = time.time(); en = translate(te); m = time.time() - t
        body = json.dumps({'te': te, 'en': en, 'asr_ms': int(a * 1000), 'mt_ms': int(m * 1000),
                           'audio_s': round(len(wav) / sr, 1)}, ensure_ascii=False).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

translate('నమస్కారం')
print('READY on :8095', flush=True)
ThreadingHTTPServer(('0.0.0.0', 8095), H).serve_forever()
