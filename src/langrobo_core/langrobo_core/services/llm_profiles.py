"""Which LLM server the brain talks to -- ONE switch, in .env. Pure zone.

    LANGROBO_LLM=gemma   Gemma 4 12B on the Mac Mini (e12b5)          -- default
    LANGROBO_LLM=qwen    Qwen3.5-9B on the laptop's RTX 4050 (qwen9b.sh)

Both serve the same OpenAI-style API (llama.cpp), so the switch is only an
address: agent_node, Studio (graph_studio.py), scripts/fleet.sh status and
scripts/llm_cache_check.py all read it from here. LANGROBO_LLM_URL overrides
the address of the chosen profile (a test server, another port). The laptop
is free only while the twin sim is down -- Isaac Sim needs its GPU.

Changing it needs a brain restart: scripts/llm_switch.sh does both steps.
Shell use: python3 -m langrobo_core.services.llm_profiles [name|url|root|slots|where]
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

PROFILES: dict[str, dict] = {
    "gemma": {"base_url": "http://singireddys-mac-mini.local:8080/v1",
              "where": "Mac Mini, Gemma 4 12B", "slots": 5,
              "start": "e12b5 in a Terminal on the Mac"},
    "qwen": {"base_url": "http://kino.local:8080/v1",
             "where": "laptop RTX 4050, Qwen3.5-9B", "slots": 4,
             "start": "~/llama.cpp-bin/qwen9b.sh on the laptop (twin sim off)"},
}
DEFAULT = "gemma"


def name() -> str:
    """The chosen profile; an unknown name falls back to the default, loudly."""
    want = (os.environ.get("LANGROBO_LLM") or DEFAULT).strip().lower()
    if want not in PROFILES:
        logger.warning("LANGROBO_LLM=%r is not one of %s -- using %r",
                       want, ", ".join(PROFILES), DEFAULT)
        return DEFAULT
    return want


def profile() -> dict:
    return PROFILES[name()]


def base_url() -> str:
    """http://host:8080/v1 of the chosen server."""
    return (os.environ.get("LANGROBO_LLM_URL") or profile()["base_url"]).rstrip("/")


def server_root() -> str:
    """http://host:8080 -- for /props, /slots, /health."""
    url = base_url()
    return url[:-3] if url.endswith("/v1") else url


def chosen() -> bool:
    """True when .env names a profile -- then it wins over the launch default."""
    return bool((os.environ.get("LANGROBO_LLM") or "").strip())


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "url"
    print({"name": name, "url": base_url, "root": server_root,
           "slots": lambda: profile()["slots"], "where": lambda: profile()["where"],
           "start": lambda: profile()["start"]}[what]())
