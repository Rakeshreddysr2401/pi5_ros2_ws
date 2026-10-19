"""StubBridge must mirror ROS2Bridge's public surface.

This is not tidiness. `langrobo_core` reaches the robot through exactly one
object it never constructs: `_bridge.get()`. On the robot that is ROS2Bridge;
in Studio, in every test, and on any laptop it is StubBridge. A method present
on one and missing on the other is a live bug that only fires off-robot — as
an AttributeError raised *inside a tool*, which LangGraph turns into a
ToolMessage and the model reports to the user as a robot fault.

That is not hypothetical: `ground_pixel` was missing from StubBridge, so
approach_described_object — the rover's only working object-approach path —
could not be exercised off-robot at all.

The check is done by parsing the AST rather than importing, because
ros2_bridge.py imports rclpy and this suite must run with no ROS2 installed.
"""

import ast
import pathlib

import pytest

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parents[3]          # tests/ -> langrobo_core/ -> src/ -> repo
_REAL = _REPO / "src/langrobo_ros/langrobo_ros/ros2_bridge.py"
_STUB = _REPO / "src/langrobo_core/langrobo_core/bridges/stub.py"


def _public_methods(path: pathlib.Path) -> set[str]:
    """Public methods and properties of the single class in `path`."""
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    return {
        n.name for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    }


@pytest.fixture(scope="module")
def surfaces():
    if not _REAL.exists():          # source checkout without langrobo_ros
        pytest.skip(f"{_REAL} not present")
    return _public_methods(_REAL), _public_methods(_STUB)


def test_stub_implements_everything_the_real_bridge_offers(surfaces):
    real, stub = surfaces
    missing = sorted(real - stub)
    assert not missing, (
        f"StubBridge is missing {missing}. Every off-robot run — Studio, this "
        f"test suite, a laptop — hits these as an AttributeError inside a tool.")


def test_stub_offers_nothing_the_real_bridge_lacks(surfaces):
    """The other direction matters too: a stub method with no real counterpart
    is a tool that works in Studio and fails on the robot."""
    real, stub = surfaces
    extra = sorted(stub - real)
    assert not extra, (
        f"StubBridge has {extra}, which ROS2Bridge does not. A tool built "
        f"against these would pass every test and break on the robot.")
