"""services/lists.py + tools/lists.py: household lists by voice."""
import pytest

from langrobo_core.services import lists
from langrobo_core.tools import lists as L


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path))


def run(**k):
    return L.household_list.func(**k)


def test_names_and_splitting():
    assert lists.list_name("My Shopping List") == "shopping"
    assert lists.list_name("groceries") == "shopping"
    assert lists.list_name("to do") == "to-do"
    assert lists.list_name("") == "shopping"
    assert lists.split_items("milk, eggs and two loaves of bread.") == ["milk", "eggs", "two loaves of bread"]


def test_add_show_remove_clear():
    assert run(action="add", items="milk, eggs and bread") == "Added milk, eggs and bread to the shopping list."
    assert run(action="add", items="Milk and rice") == "Added rice to the shopping list. Milk was already on it."
    assert run(action="show") == "The shopping list has 4: milk, eggs, bread and rice."
    assert run(action="remove", items="eggs and butter") == "Removed eggs. butter is not on the shopping list."
    assert run(action="add", items="call the plumber", name="to-do").startswith("Added call the plumber to the to-do list")
    assert run(action="show", name="garden") == "The garden list is empty. Other lists: shopping and to-do."
    assert run(action="clear") == "Cleared the shopping list (3 items)."
    assert run(action="clear") == "The shopping list was already empty."


def test_remove_by_part_of_the_name():
    run(action="add", items="two loaves of bread")
    assert run(action="remove", items="bread") == "Removed two loaves of bread."


def test_asks_when_nothing_said():
    assert run(action="add", items=" ") == "Ask what to add."


def test_on_chat():
    from langrobo_core.tools import CHAT_TOOLS
    assert "household_list" in {t.name for t in CHAT_TOOLS}
