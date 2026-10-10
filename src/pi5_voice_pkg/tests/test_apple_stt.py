"""The 'apple' provider against a stand-in mitra-stt (a local HTTP server), plus
one live call when the real one on the Mac Mini answers."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
import requests

from pi5_voice_pkg.stt_providers import REGISTRY, ProviderUnavailable

apple = REGISTRY['apple']
LIVE_URL = os.environ.get('MAC_STT_URL', 'http://singireddys-mac-mini.local:8091/stt')


def _server(status=200, body=None, auth='authorized'):
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self._send(200, {'ok': auth == 'authorized', 'auth': auth})

        def do_POST(self):
            seen['path'] = self.path
            seen['body'] = self.rfile.read(int(self.headers['Content-Length']))
            self._send(status, body if body is not None else {'transcript': ' Mitra, stop. '})

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_port}/stt', seen


def test_sends_wav_with_locale_and_name_hint():
    srv, url, seen = _server()
    try:
        p = apple.from_config({'hotwords': 'Mitra'}, {'MAC_STT_URL': url})
        assert p.transcribe(np.zeros(1600, np.float32), 16000) == 'Mitra, stop.'
        assert seen['body'][:4] == b'RIFF'
        assert 'lang=en-IN' in seen['path'] and 'Hey+Mitra' in seen['path']
    finally:
        srv.shutdown()


def test_server_error_degrades():
    srv, url, _ = _server(status=503, body={'error': 'no on-device model'})
    try:
        p = apple.from_config({}, {'MAC_STT_URL': url})
        with pytest.raises(ProviderUnavailable):
            p.transcribe(np.zeros(1600, np.float32), 16000)
    finally:
        srv.shutdown()


def test_no_permission_on_the_mac_degrades_at_startup():
    srv, url, _ = _server(auth='denied')
    try:
        with pytest.raises(ProviderUnavailable):
            apple.from_config({}, {'MAC_STT_URL': url})
    finally:
        srv.shutdown()


def test_mac_down_degrades_per_call_not_at_startup():
    p = apple.from_config({}, {'MAC_STT_URL': 'http://127.0.0.1:9/stt'})   # nothing listens
    with pytest.raises(ProviderUnavailable):
        p.transcribe(np.zeros(1600, np.float32), 16000)


def _live():
    try:
        return requests.get(LIVE_URL.rsplit('/', 1)[0] + '/health', timeout=2).json().get('ok')
    except (requests.RequestException, ValueError):
        return False


@pytest.mark.skipif(not _live(), reason='mitra-stt not running on the Mac Mini')
def test_live_silence_is_empty_not_an_error():
    p = apple.from_config({'hotwords': 'Mitra'}, {'MAC_STT_URL': LIVE_URL})
    assert p.transcribe(np.zeros(16000, np.float32), 16000) == ''
