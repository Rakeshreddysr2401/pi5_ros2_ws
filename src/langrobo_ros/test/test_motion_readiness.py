"""Real bridge readiness must never enable the stub's timed-drive fallback."""
from types import SimpleNamespace

from langrobo_ros.ros2_bridge import ROS2Bridge
from langrobo_core.tools import _bridge, movement


class MissingListener:
    def get_subscription_count(self):
        return 0

    def publish(self, msg):
        raise AssertionError("A missing motion service must receive no goal")


def test_missing_exact_move_refuses_without_timed_twist(monkeypatch):
    def not_ready(*args):
        return ROS2Bridge._run_goal_exec(
            object(), MissingListener(), 0, 0, 0, 60, (0, 0, 0))

    bridge = SimpleNamespace(
        drive_by=not_ready, turn_by=not_ready,
        clear_motion_stop=lambda: None,
        publish_twist=lambda msg: (_ for _ in ()).throw(AssertionError("timed drive")),
    )
    monkeypatch.setattr(_bridge, "get", lambda: bridge)
    monkeypatch.setattr(movement, "blocked_by_manual", lambda: None)
    monkeypatch.setattr(movement, "blocked_by_role", lambda state: None)
    for command in ("F:20", "B:10", "L:90", "R:45"):
        result = movement.move_robot.invoke({"command": command})
        assert "not_ready" in result
        assert "did NOT run" in result
        assert "Movement done" not in result


def test_stop_still_works_when_exact_move_is_missing(monkeypatch):
    sent = []
    bridge = SimpleNamespace(clear_motion_stop=lambda: None, publish_twist=sent.append)
    monkeypatch.setattr(_bridge, "get", lambda: bridge)
    result = movement.move_robot.invoke({"command": "S"})
    assert result == "Movement done: S"
    assert len(sent) == 1
    assert sent[0].linear.x == sent[0].angular.z == 0
