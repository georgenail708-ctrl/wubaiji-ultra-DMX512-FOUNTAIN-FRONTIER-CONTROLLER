"""Low-frequency installation projection from original quaternion or Euler.

Angles are installation data, not effect phase or fixture selection order.
The signed spelling of +180/-180 represents the same physical rotation.
"""
from __future__ import annotations

import math


def normalized_quaternion(value):
    """Validate scalar-last (x, y, z, w), preserving q/-q equivalence."""
    try:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        parts = tuple(float(v) for v in value)
        norm = math.hypot(*parts)
        if not all(math.isfinite(v) for v in parts) or norm < 1e-12:
            return None
        return tuple(v / norm for v in parts)
    except (TypeError, ValueError, OverflowError):
        return None


def multiply_quaternions(left, right):
    """Compose parent * child active rotations, scalar-last."""
    x, y, z, w = left
    a, b, c, d = right
    return normalized_quaternion((w*a + x*d + y*c - z*b,
                                  w*b - x*c + y*d + z*a,
                                  w*c + x*b - y*a + z*d,
                                  w*d - x*a - y*b - z*c))


def euler_quaternion(rx, ry, rz):
    """Existing Rx * Ry * Rz convention, for parents without a quat."""
    try:
        x, y, z = (math.radians(float(v)) / 2 for v in (rx, ry, rz))
        if not all(math.isfinite(v) for v in (x, y, z)):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return multiply_quaternions(
        multiply_quaternions((math.sin(x), 0, 0, math.cos(x)),
                             (0, math.sin(y), 0, math.cos(y))),
        (0, 0, math.sin(z), math.cos(z)))


def _installation_angles(fixture):
    if fixture is None:
        return None
    try:
        angles = [float(getattr(fixture, key, 0.0) or 0.0)
                  for key in ("rot_x", "rot_y", "rot_z")]
        located = any(abs(float(getattr(fixture, key, 0.0) or 0.0)) > 1e-9
                      for key in ("world_x", "world_z"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(angle) for angle in angles):
        return None
    if not located and all(abs(angle) < 1e-9 for angle in angles):
        return None
    return angles


def installation_heading(fixture) -> float | None:
    """Return the directed world XZ heading, or None for legacy missing data.

    An authoritative world Quaternion wins over potentially stale Euler fields.
    Otherwise local +X is transformed by Rz, Ry, then Rx. World coordinates
    distinguish an explicitly upright zero-angle fixture from legacy unlocated
    fixtures whose three optional rotation fields defaulted to zero.
    """
    quaternion = normalized_quaternion(getattr(fixture, "installation_quaternion", None))
    if quaternion is not None:
        qx, qy, qz, qw = quaternion
        x, z = 1 - 2 * (qy*qy + qz*qz), 2 * (qx*qz - qy*qw)
        return math.degrees(math.atan2(-z, x)) if math.hypot(x, z) >= 1e-9 else None
    angles = _installation_angles(fixture)
    if angles is None:
        return None
    rx, ry, rz = map(math.radians, angles)
    x = math.cos(ry) * math.cos(rz)
    z = math.sin(rx) * math.sin(rz) - math.cos(rx) * math.sin(ry) * math.cos(rz)
    if math.hypot(x, z) < 1e-9:
        return None
    heading = math.degrees(math.atan2(-z, x))
    return 0.0 if abs(heading) < 1e-9 else heading


def _legacy_quarter_turn(fixture):
    """Known lost-quaternion Euler spelling; zero yaw alone is never missing."""
    if normalized_quaternion(getattr(fixture, "installation_quaternion", None)) is not None:
        return False
    angles = _installation_angles(fixture)
    return bool(angles and abs(angles[1]) < 1e-9
                and all(abs((a + 180) % 360) < 1e-6 for a in (angles[0], angles[2])))


def _trusted_neighbours(fixture, companions):
    """Physical neighbours only; never feed a repaired candidate into inference."""
    x, z = fixture.world_x, fixture.world_z
    nearest = []
    if callable(companions):
        companions = companions()
    for other in companions:
        if (other.id == fixture.id or other.def_id != fixture.def_id
                or abs(other.pos_z - fixture.pos_z) > .02 or _legacy_quarter_turn(other)):
            continue
        direction = installation_heading(other)
        distance = math.hypot(other.world_x - x, other.world_z - z)
        if direction is not None and math.isfinite(distance) and distance > 1e-6:
            nearest.append((distance, other, direction))
    nearest.sort(key=lambda row: row[0])
    return nearest[:2]


def _neighbour_repair(fixture, heading, nearest):
    """Require a coherent world chord and a supported quarter-turn mismatch."""
    if len(nearest) < 2:
        return heading
    x, z = fixture.world_x, fixture.world_z
    (d1, a, ha), (d2, b, hb) = nearest[:2]
    delta = lambda left, right: abs((left - right + 180) % 360 - 180)
    # True neighbours must straddle this fixture, with a coherent tangent.
    cosine = ((a.world_x-x)*(b.world_x-x) + (a.world_z-z)*(b.world_z-z)) / (d1*d2)
    if d2 > d1 * 1.05 or cosine > math.cos(math.radians(149)) or delta(ha, hb) > 61:
        return heading
    mean = math.degrees(math.atan2(math.sin(math.radians(ha)) + math.sin(math.radians(hb)),
                                   math.cos(math.radians(ha)) + math.cos(math.radians(hb))))
    chord = math.degrees(math.atan2(-(b.world_z-a.world_z), b.world_x-a.world_x))
    if min(delta(mean, chord), delta(mean, chord+180)) > 1 or abs(delta(mean, heading)-90) > 1:
        return heading
    return mean


def synchronized_installation_heading(fixture, companions=()):
    """Strict legacy repair at Sync entry, using full-page physical neighbours.

    Old imports lost Quaternion and a cardinal Euler discontinuity.
    Recover only when two trusted, equidistant, straddling neighbours and their
    world chord independently agree on a quarter turn. Never alter raw mounts
    or infer a Quaternion. Ambiguous/isolated/legitimate zero mounts keep Euler.
    """
    heading = installation_heading(fixture)
    if heading is None or not _legacy_quarter_turn(fixture):
        return heading
    return _neighbour_repair(fixture, heading, _trusted_neighbours(fixture, companions))
