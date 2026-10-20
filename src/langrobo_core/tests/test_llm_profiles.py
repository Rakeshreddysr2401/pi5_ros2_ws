"""LANGROBO_LLM: the one switch between the Mac's Gemma and the laptop's Qwen."""

from langrobo_core.services import llm_profiles as p


def test_default_is_the_mac(monkeypatch):
    monkeypatch.delenv("LANGROBO_LLM", raising=False)
    monkeypatch.delenv("LANGROBO_LLM_URL", raising=False)
    assert p.name() == "gemma" and not p.chosen()
    assert p.base_url() == "http://singireddys-mac-mini.local:8080/v1"
    assert p.server_root() == "http://singireddys-mac-mini.local:8080"


def test_qwen_is_the_laptop(monkeypatch):
    monkeypatch.setenv("LANGROBO_LLM", " Qwen ")
    monkeypatch.delenv("LANGROBO_LLM_URL", raising=False)
    assert p.name() == "qwen" and p.chosen() and p.profile()["slots"] == 4
    assert p.base_url() == "http://kino.local:8080/v1"


def test_unknown_name_falls_back(monkeypatch):
    monkeypatch.setenv("LANGROBO_LLM", "llama")
    assert p.name() == "gemma"


def test_url_override(monkeypatch):
    monkeypatch.setenv("LANGROBO_LLM", "qwen")
    monkeypatch.setenv("LANGROBO_LLM_URL", "http://10.0.0.5:9000/v1/")
    assert p.base_url() == "http://10.0.0.5:9000/v1" and p.server_root() == "http://10.0.0.5:9000"
