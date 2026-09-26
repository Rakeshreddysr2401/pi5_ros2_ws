"""Shared test setup.

Object memory (services/object_memory) is a FILE shared by agent_node and
Studio, ~/.langrobo/object_memory.json on the robot. Any test that runs a
tool which finds something would otherwise write into the live robot's
memory -- and read whatever the robot really saw into the test. Every test
gets its own empty file instead.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_object_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "object_memory.json"))
