"""Where to look from next, when a full circle found nothing -- pure zone.

WHY (2026-10-13, owner: "when our robot is in one place of the room where it
can't see the door or any item, it searches all around 360, but when it's
still not found it needs to go to another place and find it -- whole room
search one by one"). One circle sees only what is in line of sight of one
spot: behind the sofa, round the corner of the bed, the far side of a table
are never in view. A person walks a few steps and looks again.

next_viewpoint(pose, points, visited): the LiDAR's points (odom, from
bridge.get_scan_points) say how far the robot can go in each of 12 directions
(30 deg apart): the nearest point inside a corridor HALF_W either side of the
line. A candidate is that far less CLEAR_M, at most STEP_MAX, at least
STEP_MIN. It must be NEW_M from every spot already searched (the start
included), and the best is the most new -- the farthest from all of them --
with a little credit for a longer step. It faces the way it drove (on into the
unseen part). None: no open, new direction (a small room seen whole already).

Reach plans the actual drive (nav2 + exact finish); this only picks where.
"""

import math

STEP_MAX = 2.0     # m: the furthest one move goes
STEP_MIN = 0.8     # m: a shorter move sees nothing new
CLEAR_M = 0.6      # m kept short of the nearest thing along the way
HALF_W = 0.30      # m either side of the line: the rover is ~0.38 m wide
NEW_M = 1.2        # m from every spot already searched
HEADINGS = 12
OPEN_M = 4.0       # free distance when nothing is in that direction
OWN_R = 0.25       # m: points this close are the rover's own body in the scan


def next_viewpoint(pose, points, visited):
    """pose (x, y, yaw_deg); points [(x, y)] obstacles in the same frame;
    visited [(x, y)]. Returns (x, y, yaw_deg) or None."""
    if not pose or points is None:
        return None
    x0, y0 = pose[0], pose[1]
    best, best_score = None, -1.0
    for k in range(HEADINGS):
        h = 2 * math.pi * k / HEADINGS
        c, s = math.cos(h), math.sin(h)
        free = OPEN_M
        for px, py in points:
            dx, dy = px - x0, py - y0
            if dx * dx + dy * dy < OWN_R * OWN_R:
                continue
            along = dx * c + dy * s
            if 0.0 < along < free and abs(-dx * s + dy * c) < HALF_W:
                free = along
        step = min(STEP_MAX, free - CLEAR_M)
        if step < STEP_MIN:
            continue
        cx, cy = x0 + step * c, y0 + step * s
        novelty = min((math.hypot(cx - vx, cy - vy) for vx, vy in visited), default=OPEN_M)
        if novelty < NEW_M:
            continue
        score = novelty + 0.5 * step
        if score > best_score:
            best, best_score = (cx, cy, math.degrees(h)), score
    return best
