"""approach._trail -- the find trail (2026-11-18): one log line per step, and the
judged photo kept in <state dir>/find_debug (box drawn), only the newest N."""
import io
import os

from PIL import Image

import langrobo_core.tools.approach as ap


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (64, 36)).save(b, "JPEG")
    return b.getvalue()


def test_trail_saves_judged_photos_and_keeps_only_the_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ap, "FIND_DEBUG_KEEP", 3)
    lines = []
    monkeypatch.setattr(ap.logger, "info", lambda fmt, *a: lines.append(fmt % a))
    for i in range(5):
        ap._trail(f"search{i}", "the helmet", _jpeg(), (10, 10, (2, 2, 20, 20)) if i % 2 else None)
    files = sorted(os.listdir(tmp_path / "find_debug"))
    assert len(files) == 3
    assert any(f.endswith("_found.jpg") for f in files) and any(f.endswith("_none.jpg") for f in files)
    assert lines[-1].startswith("find: search4 'the helmet'")


def test_trail_never_raises_on_a_bad_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path))
    ap._trail("arrival", "a cup", b"not a jpeg", None)      # must not raise
