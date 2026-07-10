"""MCP provider framework tests — all offline.

The token file path is injected via LANGROBO_MCP_TOKENS and the network layer
is stubbed by monkeypatching services.mcp._fetch_tools, so these tests hold
the framework's two load-bearing properties: no token → zero network, and the
guarded tools keep their LLM-visible schema byte-identical (KV-cache rule).
"""

import asyncio
import json
import os
import time

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from langrobo_core.services import mcp


@pytest.fixture(autouse=True)
def clean_mcp_state(tmp_path, monkeypatch):
    """Fresh module state + isolated token file per test."""
    token_file = tmp_path / "mcp_tokens.json"
    monkeypatch.setenv("LANGROBO_MCP_TOKENS", str(token_file))
    monkeypatch.delenv("SWIGGY_ACCESS_TOKEN", raising=False)
    mcp._tools.clear()
    mcp._headers.clear()
    mcp._stale.clear()
    mcp._nudged.clear()
    mcp._token_file_mtime = 0.0
    mcp._warned_malformed = False
    yield token_file


def _write_token_file(path, token="tok-1", domain="swiggy"):
    path.write_text(json.dumps({
        "version": 1,
        "domains": {domain: {
            "access_token": token,
            "issued_at": int(time.time()),
            "expires_at": int(time.time()) + 5 * 86400,
        }},
    }))


class _EchoArgs(BaseModel):
    query: str


def _fake_tool(fail_with: Exception | None = None):
    async def coro(**kwargs):
        if fail_with:
            raise fail_with
        return f"ok:{kwargs.get('query', '')}"
    return StructuredTool(name="search_food", description="search dishes",
                          args_schema=_EchoArgs, coroutine=coro)


@pytest.fixture
def stub_fetch(monkeypatch):
    calls = []

    def install(tools):
        async def fetch(spec, headers):
            calls.append(spec.name)
            return tools
        monkeypatch.setattr(mcp, "_fetch_tools", fetch)

    install.calls = calls
    return install


def test_registry_specs_wellformed():
    for name, spec in mcp.PROVIDERS.items():
        assert name == spec.name
        assert spec.url.startswith("https://")
        assert spec.url_env and spec.auth_domain and spec.login_hint


def test_no_token_no_network(stub_fetch):
    stub_fetch([_fake_tool()])
    assert mcp.load_provider_tools("swiggy_food") == []
    assert stub_fetch.calls == []          # never touched the network
    assert not mcp.provider_ok("swiggy_food")


def test_file_token_loads_tools(clean_mcp_state, stub_fetch):
    _write_token_file(clean_mcp_state)
    stub_fetch([_fake_tool()])
    tools = mcp.load_provider_tools("swiggy_food")
    assert len(tools) == 1
    assert mcp.provider_ok("swiggy_food")
    assert mcp._headers["swiggy_food"]["Authorization"] == "Bearer tok-1"


def test_env_token_wins_over_file(clean_mcp_state, stub_fetch, monkeypatch):
    _write_token_file(clean_mcp_state, token="file-tok")
    monkeypatch.setenv("SWIGGY_ACCESS_TOKEN", "env-tok")
    stub_fetch([_fake_tool()])
    mcp.load_provider_tools("swiggy_food")
    assert mcp._headers["swiggy_food"]["Authorization"] == "Bearer env-tok"


def test_malformed_token_file_degrades(clean_mcp_state, stub_fetch):
    clean_mcp_state.write_text("{not json")
    stub_fetch([_fake_tool()])
    assert mcp.load_provider_tools("swiggy_food") == []
    assert stub_fetch.calls == []


def test_guarded_schema_identical(clean_mcp_state, stub_fetch):
    _write_token_file(clean_mcp_state)
    original = _fake_tool()
    stub_fetch([original])
    guarded = mcp.load_provider_tools("swiggy_food")[0]
    assert guarded.name == original.name
    assert guarded.description == original.description
    assert guarded.args_schema is original.args_schema
    result = asyncio.run(guarded.coroutine(query="pizza"))
    assert result == "ok:pizza"


class _FakeTelegram:
    def __init__(self):
        self.sent = []

    def configured(self):
        return True

    def members_with_role(self, role):
        class M:
            chat_id = 42
        return [M()]

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return None


def test_guarded_401_degrades_and_nudges_once(clean_mcp_state, stub_fetch, monkeypatch):
    _write_token_file(clean_mcp_state)
    stub_fetch([_fake_tool(fail_with=Exception("401 Unauthorized"))])
    fake_tg = _FakeTelegram()
    from langrobo_core.services import telegram
    monkeypatch.setattr(telegram, "_instance", fake_tg)

    guarded = mcp.load_provider_tools("swiggy_food")[0]
    out1 = asyncio.run(guarded.coroutine(query="pizza"))
    out2 = asyncio.run(guarded.coroutine(query="pizza"))
    assert "expired" in out1 and "expired" in out2  # graceful strings, no raise
    assert not mcp.provider_ok("swiggy_food")       # prompt swap engages
    assert len(fake_tg.sent) == 1                   # exactly one nudge


def test_non_auth_error_degrades_without_staleness(clean_mcp_state, stub_fetch):
    _write_token_file(clean_mcp_state)
    stub_fetch([_fake_tool(fail_with=Exception("connection reset"))])
    guarded = mcp.load_provider_tools("swiggy_food")[0]
    out = asyncio.run(guarded.coroutine(query="pizza"))
    assert "failed" in out
    assert mcp.provider_ok("swiggy_food")           # transient error ≠ dead login


def test_refresh_rearms_stale_provider(clean_mcp_state, stub_fetch):
    _write_token_file(clean_mcp_state, token="old-tok")
    stub_fetch([_fake_tool()])
    mcp.load_provider_tools("swiggy_food")
    headers = mcp._headers["swiggy_food"]

    mcp.mark_domain_stale("swiggy", "401")
    assert not mcp.provider_ok("swiggy_food")

    _write_token_file(clean_mcp_state, token="new-tok")
    os.utime(clean_mcp_state, (time.time() + 5, time.time() + 5))
    mcp.refresh_tokens_if_changed()

    assert headers["Authorization"] == "Bearer new-tok"  # same dict, mutated
    assert mcp.provider_ok("swiggy_food")
    assert "swiggy" not in mcp._nudged


def test_status_reports_without_secrets(clean_mcp_state, stub_fetch):
    _write_token_file(clean_mcp_state, token="secret-token-value")
    stub_fetch([_fake_tool()])
    mcp.load_provider_tools("swiggy_food")
    st = mcp.status()
    assert st["providers"]["swiggy_food"]["tools"] == 1
    assert st["tokens"]["swiggy"]["source"] == "file"
    assert st["tokens"]["swiggy"]["days_left"] == pytest.approx(5.0, abs=0.1)
    assert "secret-token-value" not in json.dumps(st)
