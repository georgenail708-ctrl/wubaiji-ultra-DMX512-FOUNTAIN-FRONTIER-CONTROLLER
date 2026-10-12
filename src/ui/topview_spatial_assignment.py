"""Transient exact segment candidates for dense TopView spline assignment.

SciPy 1.17.1 cKDTree (BSD-3-Clause) supplies candidates only. The original
scalar distance decides membership; other geometry retains its original path.
"""

import math
from itertools import chain


def _prepared_tolerance(panel, shape):
    try:
        return float(panel._shape_assign_tolerance(shape))
    except Exception:
        return None


def _segment_square(segment):
    ax, ay, bx, by = segment
    square = (bx - ax) ** 2 + (by - ay) ** 2
    if square == 0.0 and (ax != bx or ay != by):
        raise ValueError("Underflow requires the original scalar path")
    return square


class _SegmentIndex:
    def __init__(self, points, legacy):
        from scipy.spatial import cKDTree

        self.legacy = legacy
        self.segments = [(a[0], a[1], b[0], b[1])
                         for a, b in zip(points, points[1:])]
        self.coordinate_scale = max(1.0, max(map(abs, chain.from_iterable(points))))
        if self.coordinate_scale > 1e150:
            raise ValueError("Extreme coordinates require the original path")
        # Preserve original exceptional arithmetic even for a distant segment.
        list(map(_segment_square, self.segments))
        centers = [(ax + (bx - ax) * .5, ay + (by - ay) * .5)
                   for ax, ay, bx, by in self.segments]
        self.max_half_length = max(math.hypot(bx - ax, by - ay) * .5
                                   for ax, ay, bx, by in self.segments)
        self.tree = cKDTree(centers, copy_data=True)

    def distance(self, pos):
        point = (pos.x(), pos.y())
        if max(map(abs, point)) > 1e150:
            raise ValueError("Extreme query requires the original path")
        _, nearest = self.tree.query(point, k=1, eps=0.0, workers=1)
        exact = self.legacy._dist_point_to_segment
        upper = exact(*point, *self.segments[int(nearest)])
        radius = upper + self.max_half_length
        if not math.isfinite(radius):
            raise ValueError("Nonfinite bound requires the original path")
        # Triangle inequality includes every segment that can beat this upper
        # bound. Coordinate-aware expansion conservatively covers float error.
        margin = max(self.coordinate_scale, abs(radius), *map(abs, point)) * 1e-9
        candidates = self.tree.query_ball_point(
            point, radius + margin, eps=0.0, workers=1, return_sorted=True)
        return min(exact(*point, *self.segments[index]) for index in candidates)


def _curve_index(shape, legacy):
    try:
        if shape.get("type") != "spline" or shape.get("seg_cps"):
            return None
        points = shape.get("points", []) or []
        if len(points) < 65:
            return None
        points = [(legacy._safe_float(x), legacy._safe_float(y)) for x, y in points]
        if not all(map(math.isfinite, chain.from_iterable(points))):
            return None
        return _SegmentIndex(points, legacy)
    except Exception:
        return None


class ShapeMeasurements:
    """One cache-miss preparation; no persistent author or geometry state."""

    def __init__(self, panel, shapes):
        from . import topview_panel as legacy

        self.legacy = legacy
        self.shapes = shapes
        self.tolerances = [_prepared_tolerance(panel, shape) for shape in shapes]
        self.curves = [_curve_index(shape, legacy) for shape in shapes]

    def tolerance(self, index):
        value = self.tolerances[index]
        if value is None:
            raise ValueError("Original shape tolerance failed")
        return value

    def measure(self, index, pos):
        shape = self.shapes[index]
        tol = self.tolerance(index)
        inside = bool(self.legacy._shape_body_contains(shape, pos, padding=tol))
        curve = self.curves[index]
        if curve is not None:
            try:
                return inside, float(curve.distance(pos))
            except Exception:
                pass
        return inside, float(self.legacy._shape_hit_distance(shape, pos))
