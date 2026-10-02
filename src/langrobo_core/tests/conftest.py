"""Shared test setup.

Object memory (services/object_memory) is a FILE shared by agent_node and
Studio, ~/.langrobo/object_memory.json on the robot. Any test that runs a
tool which finds something would otherwise write into the live robot's
memory -- and read whatever the robot really saw into the test. Every test
gets its own empty file instead.
"""

import os

import pytest

# Tests never trace. A LangSmith key exported in the developer's shell would
# otherwise send every test's fake LLM run to the robot's real project.
for _k in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2"):
    os.environ.pop(_k, None)
os.environ.pop("LANGROBO_TRACING", None)


@pytest.fixture(autouse=True)
def _isolated_object_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "object_memory.json"))


@pytest.fixture(autouse=True)
def _no_background_photo_survey(request, monkeypatch):
    """Tools submit every photo to the background survey (tools/survey.py),
    whose worker calls the real vision model. Tests queue nothing -- except
    test_survey.py, which tests the survey itself with its own fakes."""
    if request.module.__name__.endswith("test_survey"):
        return
    from langrobo_core.tools import survey
    monkeypatch.setattr(survey, "submit", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _teleop_reads_auto(request, monkeypatch):
    """The MANUAL guard asks the LIVE teleop page (127.0.0.1:8091) before any
    move. On the robot that page is up, so the whole motion suite passed or
    failed with the real switch: 22 tests failed on 2026-10-02 only because
    someone had left it in MANUAL. Every test sees AUTO -- except
    test_manual_guard.py, which sets the switch itself (and reads the real
    function on purpose)."""
    if request.module.__name__.endswith("test_manual_guard"):
        return
    from langrobo_core.tools import movement
    monkeypatch.setattr(movement, "teleop_is_manual", lambda: False)
