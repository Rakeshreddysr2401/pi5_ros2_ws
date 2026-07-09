#!/usr/bin/env python3
"""Debug the supervisor KV-slot reuse (TODO.md: full prefill on [SYSTEM] turns).

Two checks, no robot needed (pure langrobo_core — run from a venv on any machine,
or on the Pi5 with the workspace sourced):

1. OFFLINE payload diff (always runs): drives the REAL graph through two
   consecutive [SYSTEM] reminder turns with scripted LLM responses and a
   production-shaped history (tool calls, camera frame, routing notes), then
   checks the two supervisor request bodies for the append-only prefix
   property. A divergence here = client-side cache-buster; prints the first
   differing message.

2. LIVE replay (--server URL, e.g. http://singireddys-mac-mini.local:8080):
   POSTs payload A, A again, then B to the real llama.cpp (id_slot as built,
   max_tokens=1) and prints prompt_n / cache_n per request. Expected healthy:
   A=cold, A-repeat≈tiny, B≈just-the-tail. B≈full-prompt = server-side
   reuse failure reproduced.

Verified 2026-07-10 (laptop + live Mac Mini): both checks PASS with current
code — payloads are append-only and the server reuses them (A repeat: 32
tokens, B: 180 = tail only). See TODO.md for what that rules out.
"""

import argparse
import json
import os
import sys
import urllib.request

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")


def build_supervisor_payloads():
    """Run two [SYSTEM] turns through the real graph; return the two payloads."""
    from langchain_core.messages import (
        AIMessage, HumanMessage, SystemMessage, ToolMessage,
    )
    from langrobo_core.services import llm as llm_module
    from langrobo_core.tools import _bridge as bridge_module
    from langrobo_core.bridges import StubBridge

    # Same slot wiring as agent_node.py
    spec = {"slot": 2}
    llm_module.configure(
        "llamacpp", "gemma", "http://offline-diff:8080/v1", "none", 3000,
        {
            "local_agent": {"slot": 1},
            "supervisor": {"streaming": False, "slot": 3},
            "navigate": dict(spec), "status": dict(spec), "swiggy": dict(spec),
            "tracker": dict(spec), "knowledge": dict(spec), "briefing": dict(spec),
            "consolidation": {"streaming": False, **spec},
        },
        strict_tools=True, streaming=True, slot=0,
    )
    bridge_module.init(StubBridge(known_locations={"kitchen": (0, 0, 0)}))

    from langrobo_core.utils import message_utils
    from langrobo_core.graph import build_graph
    import langrobo_core.agents.supervisor as sup_mod
    import langrobo_core.agents.chat as chat_mod
    from langrobo_core.utils.history import trim_history

    captured, call_n = [], [0]

    def scripted_safe_invoke(llm, messages, logger, retries=1):
        call_n[0] += 1
        bound = getattr(llm, "bound", llm)
        kwargs = dict(getattr(llm, "kwargs", {}) or {})
        payload = bound._get_request_payload(messages, stop=None, **kwargs)
        tc = payload.get("tool_choice")
        is_sup = isinstance(tc, dict) and tc.get("function", {}).get("name") == "handover"
        captured.append(("supervisor" if is_sup else "agent", payload))
        if is_sup:
            return AIMessage(content="", tool_calls=[{
                "name": "handover",
                "args": {"next_agent": "chat", "reason": "reminder due"},
                "id": f"call_sup_{call_n[0]}",
            }])
        return AIMessage(content=f"Here's your reminder! (call {call_n[0]})")

    message_utils.safe_invoke = scripted_safe_invoke
    sup_mod.safe_invoke = scripted_safe_invoke
    chat_mod.safe_invoke = scripted_safe_invoke

    graph = build_graph()

    # Production-shaped history: tool turns, a look() frame, old routing notes.
    jpeg = "aGVsbG8=" * 50
    history = [
        HumanMessage(content="hello"),
        AIMessage(content="Hi! How can I help?"),
        HumanMessage(content="what time is it?"),
        AIMessage(content="", tool_calls=[{
            "name": "get_current_time", "args": {}, "id": "call_time_1"}]),
        ToolMessage(content="4:30 AM", name="get_current_time",
                    tool_call_id="call_time_1"),
        AIMessage(content="It's 4:30 AM."),
        HumanMessage(content="what do you see?"),
        SystemMessage(content="[Routing note] Control passed to the 'local_agent' "
                              "agent because: \"visual query\"."),
        HumanMessage(content=[
            {"type": "text", "text": "[Current camera view]"},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{jpeg}"}},
        ]),
        AIMessage(content="I see a desk with a laptop on it."),
        HumanMessage(content="set a timer for one minute"),
        AIMessage(content="", tool_calls=[{
            "name": "set_reminder", "args": {"text": "timer", "in_minutes": 1},
            "id": "call_rem_1"}]),
        ToolMessage(content="Reminder set", name="set_reminder",
                    tool_call_id="call_rem_1"),
        AIMessage(content="Done — I'll remind you in one minute."),
    ]

    for reminder in (
        "[SYSTEM] Reminder due — announce to the user now: Your 1 minute timer is done",
        "[SYSTEM] Reminder due — announce to the user now: Your 2 minute timer is done",
    ):
        result = None
        for event in graph.stream(
            {"messages": history + [HumanMessage(content=reminder)],
             "active_agent": "supervisor", "channel": "system",
             "sender_name": None, "sender_role": None},
            stream_mode="values",
        ):
            result = event
        history, _ = trim_history(result["messages"], max_len=200)

    sups = [p for label, p in captured if label == "supervisor"]
    assert len(sups) == 2, f"expected 2 supervisor calls, got {len(sups)}"
    return sups


def check_prefix(p1, p2) -> bool:
    """True if p2 is p1 + appended messages with identical body fields."""
    ok = True
    b1 = {k: v for k, v in p1.items() if k != "messages"}
    b2 = {k: v for k, v in p2.items() if k != "messages"}
    if json.dumps(b1, sort_keys=True, default=str) != json.dumps(b2, sort_keys=True, default=str):
        print("!! non-message body fields differ between consecutive supervisor calls")
        ok = False
    m1, m2 = p1["messages"], p2["messages"]
    for i in range(min(len(m1), len(m2))):
        a = json.dumps(m1[i], sort_keys=True, default=str)
        b = json.dumps(m2[i], sort_keys=True, default=str)
        if a != b:
            print(f"!! FIRST DIVERGENCE at message index {i}:")
            print(f"  call1[{i}]: {a[:400]}")
            print(f"  call2[{i}]: {b[:400]}")
            return False
    if len(m1) > len(m2):
        print("!! second call has FEWER messages — history was dropped")
        return False
    print(f"prefix property HOLDS: call2 = call1 ({len(m1)} msgs) "
          f"+ {len(m2) - len(m1)} appended")
    return ok


def replay(server: str, payloads):
    """POST A, A, B to the live server; print prompt-processing stats."""
    a, b = payloads
    for label, payload in (("A (cold)", a), ("A (repeat)", a), ("B (tail-only?)", b)):
        body = dict(payload)
        body["max_tokens"] = 1
        body["stream"] = False
        req = urllib.request.Request(
            f"{server.rstrip('/')}/v1/chat/completions",
            data=json.dumps(body, default=str).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            r = json.load(resp)
        t = r.get("timings", {})
        print(f"  {label:16s} prompt_n={t.get('prompt_n')} "
              f"cache_n={t.get('cache_n', 'n/a')} "
              f"prompt_ms={round(t.get('prompt_ms', 0))}")
    print("healthy = repeat & B small; B ≈ full prompt = reuse failure reproduced")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", metavar="URL", default=None,
                    help="llama.cpp base URL for live replay "
                         "(e.g. http://singireddys-mac-mini.local:8080). "
                         "Replays on id_slot=3 — evicts only the supervisor slot.")
    args = ap.parse_args()

    print("== offline payload diff (real graph, scripted LLM) ==")
    payloads = build_supervisor_payloads()
    ok = check_prefix(*payloads)

    if args.server:
        print(f"\n== live replay against {args.server} ==")
        replay(args.server, payloads)

    sys.exit(0 if ok else 1)
