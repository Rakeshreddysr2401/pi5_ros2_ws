#!/usr/bin/env python3
"""Does the real LLM pick the right first step with the brain's real prompts?

Sends each agent's rendered system prompt + bound tool schemas (exactly what
agents/factory.py builds) to the llama.cpp server and checks the FIRST thing
the model does: which tool, which handover target, plain text or [silent].
Three sets:

  turns    single user turns into each agent (incl. navigate right after a
           drive -- the sticky follow-up that used to go wrong)
  hops     a real handover (handle_handover's routing note), then the target
  arrival  [SYSTEM] arrival turns with an errand (`then`), entering chat

Run it after any change to prompts.py, a tool docstring or a tool set.
Baseline 2026-10-21 (Gemma 4 12B, e12b5): turns 30/30, hops 8/8, arrival 3/3
(the prompts before that day: turns 26/30).

Uses KV slot 4 (the idle voice-filter slot), so the live brain's slots 0-3
keep their caches. No robot, no ROS. Each agent's first case re-reads its
prompt (~20-30 s); the rest take ~2-3 s each.

  python3 scripts/gemma_flow_eval.py                # all three sets
  python3 scripts/gemma_flow_eval.py turns --agent navigate
  python3 scripts/gemma_flow_eval.py --base-url http://host:8080/v1 --slot 4
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "langrobo_core"))
os.environ.setdefault("LANGROBO_FOLLOW", "1")          # production binds follow_person
os.environ["LANGCHAIN_TRACING_V2"] = "false"          # never traces
os.environ["LANGSMITH_TRACING"] = "false"

from dotenv import load_dotenv                         # noqa: E402
load_dotenv(ROOT / ".env")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from langchain_openai import ChatOpenAI                # noqa: E402

from langrobo_core.graph.handover_resolver import handle_handover   # noqa: E402
from langrobo_core.prompts import render_tools, render_web_rules    # noqa: E402
from langrobo_core.registry import SPECS                            # noqa: E402
from langrobo_core.tools.handover import handover                   # noqa: E402
from langrobo_core.utils.message_utils import prepare_messages_for_agent  # noqa: E402

T = "[Time now: 4:30 PM, say: half past four in the afternoon] "
POSE = "[Robot now at x=1.20 y=0.34 heading=45°] "


# ── helpers ──────────────────────────────────────────────────────────────────

def first_step(resp):
    """'tool_name' | 'handover:<agent>' | 'text' | 'silent', and the detail."""
    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc["name"] == "handover":
            return f"handover:{tc['args'].get('next_agent')}", tc
        return tc["name"], tc
    txt = resp.content if isinstance(resp.content, str) else str(resp.content)
    return ("silent" if txt.strip().strip(".").lower() == "[silent]" else "text"), txt


def is_call(name, **want):
    """Check: the first step is tool `name` (args matching `want`, if given)."""
    def check(resp):
        if not resp.tool_calls or resp.tool_calls[0]["name"] != name:
            return False
        args = resp.tool_calls[0]["args"]
        return all(f(args.get(k)) if callable(f) else args.get(k) == f for k, f in want.items())
    return check


def after_move():
    """navigate just drove: the next turn re-enters it (sticky)."""
    return [HumanMessage(T + "move forward 50 centimetres"),
            AIMessage("", tool_calls=[{"name": "move_robot", "args": {"command": "F:50"},
                                       "id": "m1", "type": "tool_call"}]),
            ToolMessage("Movement done: F:50.", tool_call_id="m1", name="move_robot"),
            AIMessage("Moved forward 50 centimetres.")]


def hop(src, utt, target, reason, said=""):
    """History after `src` handed over: the real routing note included."""
    args = {"next_agent": target, "reason": reason}
    msgs = [HumanMessage(T + utt),
            AIMessage(said, tool_calls=[{"name": "handover", "args": args,
                                         "id": "h1", "type": "tool_call"}]),
            ToolMessage(handover.invoke(args), tool_call_id="h1", name="handover")]
    cmd = handle_handover({"messages": msgs, "active_agent": src, "agent_turn_visits": {}})
    assert cmd.goto == target, cmd
    return msgs + cmd.update["messages"]


def arrival(where, errand):
    """The [SYSTEM] arrival turn, with movement.arrival_task_note's wording."""
    return [HumanMessage(T + f"[SYSTEM] {where} You were asked to do this on arrival: "
                         f"{errand}. Do it now -- it is part of the user's request, not a new one.")]


# ── the cases: (agent, label, history, expected) ─────────────────────────────
# expected: "tool|other_tool" / "handover:<agent>" / "text" / "silent", or a check fn.

def turns():
    u = lambda agent, text: [HumanMessage(T + ("" if agent == "chat" else POSE) + text)]
    cases = [
        ("chat", "what's the weather in Hyderabad today?", "tavily_search"),
        ("chat", "tell mom I'll be late tonight", "send_telegram_message"),
        ("chat", "go near the red bag", "handover:navigate"),
        ("chat", "what do you see?", "handover:local_agent"),
        ("chat", "Kino, what is behind you?", "handover:local_agent"),
        ("chat", "look around the room and tell me what's here", "handover:navigate"),
        ("chat", "where did you see my bag?", "ask_photos"),
        ("chat", "remember that amma likes filter coffee", "memory"),
        ("chat", "set a timer for ten minutes for the tea", "set_reminder"),
        ("chat", "what is twelve times seven?", "text"),
        ("chat", "Ravi, did you pay the electricity bill yesterday?", "silent"),
        ("chat", "Kino come here", "handover:navigate"),
        ("chat", "what am I holding?", "handover:local_agent"),
        ("chat", "how is your battery?", "get_robot_status"),
        ("chat", "find my water bottle and go near it", "handover:navigate"),
        ("local_agent", "what do you see?", "look"),
        ("local_agent", "how far is the chair from you?", "locate_object"),
        ("local_agent", "go near the chair", "handover:navigate"),
        ("local_agent", "what's the weather tomorrow?", "handover:chat"),
        ("local_agent", "what is behind you?", "look_toward"),
        ("local_agent", "look around the whole room", "handover:navigate"),
    ]
    out = [(a, t, u(a, t), e) for a, t, e in cases]
    nav = [
        ("forward 60 then turn left then forward 30",
         is_call("move_robot", command=lambda c: (c or "").replace(" ", "") == "F:60,L:90,F:30")),
        ("go near the red bottle and tell me what is on it",
         is_call("approach_described_object", then=bool)),
        ("what do you see now?", "handover:local_agent"),
        ("what's the weather like?", "handover:chat"),
        ("where are you now?", "get_robot_status"),
        ("look around", "scan_surroundings"),
        ("turn around", "move_robot"),
        ("set a timer for five minutes", "handover:chat"),
        ("go to the kitchen", "navigate_to_pose|list_saved_locations"),
    ]
    out += [("navigate", t, after_move() + u("navigate", t), e) for t, e in nav]
    return out


def hops():
    return [
        ("navigate", "chat -> navigate",
         hop("chat", "find my black backpack and go near it", "navigate", "go near the black backpack"),
         "approach_described_object"),
        ("navigate", "chat -> navigate (spoke first)",
         hop("chat", "go near the sofa and send me a pic", "navigate",
             "go near the sofa, then send a photo", said="Let me check."),
         is_call("approach_described_object", then=lambda t: bool(t) and "(" not in t)),
        ("local_agent", "chat -> local_agent",
         hop("chat", "what am I holding?", "local_agent", "what is the user holding"), "look"),
        ("navigate", "local_agent -> navigate",
         hop("local_agent", "go near it", "navigate", "go near the red water bottle on the desk to the left"),
         is_call("approach_described_object", description=lambda d: "bottle" in (d or ""))),
        ("chat", "navigate -> chat",
         hop("navigate", "set a timer for five minutes", "chat", "changed topic"), "set_reminder"),
        ("local_agent", "navigate -> local_agent",
         hop("navigate", "what do you see now?", "local_agent", "what do you see now"), "look"),
        ("navigate", "follow: come here",
         [HumanMessage(T + POSE + "Kino come here")], is_call("follow_person", mode="come")),
        ("navigate", "follow: follow me",
         [HumanMessage(T + POSE + "follow me")], "follow_person"),
        ("navigate", "follow: follow me (Telegram)",
         [HumanMessage(T + POSE + "[Telegram from Rakesh] follow me")],
         is_call("follow_person", mode=lambda m: m in (None, "follow"))),
    ]


def arrivals():
    return [
        ("chat", "arrival: tell what is on the box",
         arrival("I've arrived at 'near the box'.", "tell the user what is on the box"),
         "handover:local_agent"),
        ("chat", "arrival: send a photo",
         arrival("I've arrived at 'near the sofa'.", "send Rakesh a photo of the sofa"),
         "send_telegram_photo"),
        ("chat", "arrival: say it in the room",
         arrival("I've reached the person.", "say out loud to her: Rakesh says dinner is ready"),
         "say_in_room"),
    ]


SETS = {"turns": turns, "hops": hops, "arrival": arrivals}


# ── run ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sets", nargs="*", choices=[*SETS, []], help="default: all")
    ap.add_argument("--agent", help="only cases entering this agent")
    ap.add_argument("--base-url", default=os.environ.get(
        "LANGROBO_EVAL_URL", "http://singireddys-mac-mini.local:8080/v1"))
    ap.add_argument("--slot", type=int, default=4, help="llama.cpp id_slot (default 4, idle)")
    args = ap.parse_args()

    llm = ChatOpenAI(model="default", api_key="none", temperature=0, max_tokens=300,
                     base_url=args.base_url, extra_body={"id_slot": args.slot},
                     timeout=180, max_retries=0)

    def ask(agent, msgs):
        spec = SPECS[agent]
        prompt = (spec.prompt.replace("{tools}", render_tools(spec.tools))
                  .replace("{web}", render_web_rules(spec.tools)))
        if spec.context:
            prompt += spec.context()
        clean = prepare_messages_for_agent(msgs, keep_images=spec.keep_images)
        return llm.bind_tools(spec.tools).invoke([SystemMessage(prompt)] + clean)

    total = passed = 0
    for name in args.sets or list(SETS):
        cases = [c for c in SETS[name]() if not args.agent or c[0] == args.agent]
        ok = 0
        print(f"== {name} ==")
        for agent, label, msgs, want in cases:
            resp = ask(agent, msgs)
            got, detail = first_step(resp)
            good = want(resp) if callable(want) else got in want.split("|")
            ok += good
            shown = "check" if callable(want) else want
            print(f"{'PASS' if good else 'FAIL'} {agent:11} {label[:48]:48} want {shown:24} got {got}",
                  flush=True)
            if not good:
                print("      ", str(detail)[:200])
        print(f"{name}: {ok}/{len(cases)}\n")
        total, passed = total + len(cases), passed + ok
    print(f"TOTAL {passed}/{total}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
