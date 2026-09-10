"""utils/pose_stamp — the stamps that make a stale camera view visibly stale.

The defect these guard: look() labelled its frame "[Current camera view]" and
never rewrote it, so after a drive the model answered from a photo of a place
the robot had left, with the label still calling it current. The fix is not a
louder instruction -- it is two poses in the context that disagree.
"""

import pytest

from langrobo_core.utils import pose_stamp as ps


@pytest.fixture(autouse=True)
def _clean():
    ps.forget_view()
    yield
    ps.forget_view()


# ── The label never claims to be current ────────────────────────────────────

def test_the_label_says_where_not_when_it_is_current():
    label = ps.view_label((1.2, 0.34, 45.0))
    assert "current" not in label.lower(), \
        "the whole bug was a label that asserted currency and never expired"
    assert "x=1.20" in label and "heading=45" in label


def test_no_pose_says_so_rather_than_inventing_one():
    """The real bridge returns None when TF has no odom->base_link fix. A made-up
    position would be worse than an honest gap -- it would compare as 'unmoved'."""
    label = ps.view_label(None)
    assert "position unknown" in label
    assert "x=" not in label


def test_history_can_still_recognise_the_stamped_frame():
    """utils/history must never cut a conversation at a camera frame, and it
    finds them by this marker. A stamp it cannot match silently re-enables that
    bug."""
    from langrobo_core.utils.history import is_camera_frame
    from langchain_core.messages import HumanMessage
    msg = HumanMessage(content=[
        {"type": "text", "text": ps.view_label((0.0, 0.0, 0.0))},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,X"}},
    ])
    assert is_camera_frame(msg)


# ── The turn stamp ──────────────────────────────────────────────────────────

def test_no_stamp_before_anything_has_been_looked_at():
    """Stamping "what's the weather" with a pose is noise on every text turn,
    and there is no photo yet for it to be stale against."""
    assert ps.turn_stamp((1.0, 2.0, 30.0)) is None


def test_standing_still_reads_as_unmoved():
    ps.record_view((1.0, 0.5, 20.0))
    stamp = ps.turn_stamp((1.02, 0.51, 21.0))
    assert "unmoved" in stamp


def test_a_drive_says_how_far_from_the_view():
    ps.record_view((1.0, 0.0, 0.0))
    stamp = ps.turn_stamp((1.9, 0.0, 180.0))
    assert "0.90 m" in stamp and "180" in stamp
    assert "has left" in stamp


def test_a_pure_turn_counts_as_moved():
    """The rover pivots in place. The position is identical and the view is
    completely different -- distance alone would call this unmoved."""
    ps.record_view((1.0, 0.0, 0.0))
    stamp = ps.turn_stamp((1.0, 0.0, 90.0))
    assert "unmoved" not in stamp


def test_losing_localisation_does_not_read_as_unmoved():
    """TF dropping out must not silently license the old photo."""
    ps.record_view((1.0, 0.0, 0.0))
    stamp = ps.turn_stamp(None)
    assert "unknown" in stamp and "unmoved" not in stamp


# ── Angles ──────────────────────────────────────────────────────────────────

def test_headings_wrap_the_short_way():
    """359 deg and 1 deg are 2 deg apart, not 358. Without this every crossing
    of the wrap point reads as a half-turn and forces a needless 10-40s look."""
    assert ps.delta((0, 0, 359.0), (0, 0, 1.0))[1] == pytest.approx(2.0)
    assert not ps.has_moved((0, 0, 359.0), (0, 0, 1.0))


def test_the_label_and_the_stamp_wrap_the_same_way():
    """Both sides of the comparison must print the same convention, or the model
    is asked to compare 225 with -135 and reasonably concludes they differ."""
    assert "heading=-135" in ps.view_label((0.0, 0.0, 225.0))
    ps.record_view((0.0, 0.0, 225.0))
    assert "heading=-135" in ps.turn_stamp((0.0, 0.0, 225.0))
