"""household_list() -- shopping, to-do and any named list. Pure zone.
The store is services/lists.py. Anyone in the house may use it (CAP_CHAT)."""

from typing import Literal

from langchain_core.tools import tool

from ..services import lists


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


@tool
def household_list(action: Literal["add", "remove", "show", "clear"], items: str = "",
                   name: str = "shopping") -> str:
    """A household list: add / remove items ("milk, eggs"), show it, or clear
    it. name: "shopping" (default), "to-do", or any list the user names."""
    lname = lists.list_name(name)
    if action in ("add", "remove") and not lists.split_items(items):
        return f"Ask what to {action}."
    if action == "add":
        added, dupes = lists.add(lname, items)
        out = f"Added {_join(added)} to the {lname} list." if added else ""
        if dupes:
            out += f" {_join(dupes)} {'was' if len(dupes) == 1 else 'were'} already on it."
        return out.strip()
    if action == "remove":
        removed, missing = lists.remove(lname, items)
        out = f"Removed {_join(removed)}." if removed else ""
        if missing:
            out += f" {_join(missing)} {'is' if len(missing) == 1 else 'are'} not on the {lname} list."
        return out.strip()
    if action == "clear":
        n = lists.clear(lname)
        return f"Cleared the {lname} list ({n} items)." if n else f"The {lname} list was already empty."
    got = lists.show(lname)
    if got:
        return f"The {lname} list has {len(got)}: {_join(got)}."
    others = [n for n in lists.names() if n != lname]
    return f"The {lname} list is empty." + (f" Other lists: {_join(others)}." if others else "")


LIST_TOOLS = [household_list]
