"""LANGROBO_STATE_DIR: a second brain (the Mitra twin, scripts/twin_brain.sh)
keeps everything it learns apart from the real robot's ~/.langrobo."""
import os

from langrobo_core.services import object_memory
from langrobo_core.services.config import state_path


def test_default_is_home_langrobo(monkeypatch):
    monkeypatch.delenv("LANGROBO_STATE_DIR", raising=False)
    assert state_path() == os.path.expanduser("~/.langrobo")
    assert state_path("locations.json") == os.path.expanduser("~/.langrobo/locations.json")


def test_state_dir_moves_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path / "twin"))
    monkeypatch.delenv("LANGROBO_OBJECT_MEMORY", raising=False)
    assert state_path("locations.json") == str(tmp_path / "twin" / "locations.json")
    assert object_memory.path() == str(tmp_path / "twin" / "object_memory.json")


def test_telegram_paths_follow(monkeypatch, tmp_path):
    from langrobo_core.services.config import TelegramConfig
    from langrobo_core.services.telegram import TelegramService
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path))
    svc = TelegramService(TelegramConfig())
    assert svc._offset_path == str(tmp_path / "telegram_offset")
    assert svc._deferred_path == str(tmp_path / "telegram_deferred.json")


def test_explicit_object_memory_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path / "twin"))
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "om.json"))
    assert object_memory.path() == str(tmp_path / "om.json")
