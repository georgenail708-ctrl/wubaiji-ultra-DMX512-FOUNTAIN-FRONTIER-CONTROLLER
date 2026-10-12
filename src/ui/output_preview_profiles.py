"""Compile optional physical metadata; never infer metres from fixture names.

nullable physical ranges / typed motors: PV-REAL reference report.
All positions crossing the renderer boundary use metres (display scale only
when provenance is missing). This adapter neither edits nor evaluates a show.
"""
from math import isfinite


def number(value, default=0.0):
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def vector(value, default=(0, 0, 0)):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        value = default
    return tuple(number(v) for v in value)


def motor_profile(kind, profile, ranges):
    entries = profile.get("motors")
    value = entries.get(kind) if isinstance(entries, dict) else None
    entry = dict(value) if isinstance(value, dict) else {}
    known_range = kind in ranges
    role = entry.get("role", "rotate" if kind != "motor_z" or known_range else "unknown")
    lo, hi = ranges.get(kind, (-180, 180) if role == "rotate" else (0, 1))
    axis = entry.get("axis", {"motor_x": (1, 0, 0), "motor_y": (0, 0, 1), "motor_z": (0, 1, 0)}[kind])
    if isinstance(axis, int):
        axis = tuple(int(i == axis) for i in range(3))
    return {"role": role, "axis": vector(axis, (0, 1, 0)),
            "min": number(entry.get("min", lo)), "max": number(entry.get("max", hi)),
            "unit": entry.get("unit", "deg" if role == "rotate" else "relative"),
            "invert": bool(entry.get("invert", False)),
            "source_unit": entry.get("source_unit", entry.get("unit", "unknown")),
            "calibrated": bool(entry.get("calibrated", False)),
            "source": entry.get("source", "imported range" if known_range else "relative")}


def compile_profile(fixture, definition):
    value = getattr(fixture, "preview_physical", None)
    profile = dict(value) if isinstance(value, dict) else {}
    local = tuple(number(getattr(fixture, name, 0)) / 100
                  for name in ("pos_x", "pos_z", "pos_y"))
    position = vector(profile.get("world_position_m"), local) if profile.get("world_valid") else local
    offset = vector(profile.get("anchor_m"))
    channels = {str(getattr(ch, "value", ch)).split("#", 1)[0]
                for ch in getattr(definition, "channels", ())}
    ranges = getattr(definition, "motor_ranges", {}) or {}
    motors = {kind: motor_profile(kind, profile, ranges)
              for kind in ("motor_x", "motor_y", "motor_z") if kind in channels}
    value = profile.get("water")
    water = dict(value) if isinstance(value, dict) else {}
    water.setdefault("role", "jet" if "pump" in channels else "unknown")
    water.setdefault("source", "relative")
    water.setdefault("pump_channel", "pump")
    water.setdefault("valve_channel", "valve")
    water["height_known"] = number(water.get("max_height_m")) > 0 and bool(water.get("calibrated"))
    water["display_height"] = number(water.get("max_height_m"), 3.0)
    relative_height = number(water.get("relative_display_height"))
    if relative_height > 0:
        water["display_height"] = relative_height
        water["height_known"] = False
    if water["display_height"] <= 0:
        water["display_height"] = 3.0
    value = profile.get("beam")
    beam = dict(value) if isinstance(value, dict) else {}
    beam["angle_deg"] = max(1, min(90, number(beam.get("angle_deg"), 12)))
    beam["length_m"] = max(.1, number(beam.get("length_m"), 4))
    rotation = tuple(number(getattr(fixture, name, 0)) for name in ("rot_x", "rot_y", "rot_z"))
    return {"position": position, "anchor": tuple(a + b for a, b in zip(position, offset)),
            "rotation": vector(profile.get("rotation_deg"), rotation), "motors": motors,
            "water": water, "beam": beam,
            "source": profile.get("source", "local / uncalibrated"),
            "world_valid": bool(profile.get("world_valid"))}


def open_gate(raw, ranges):
    return any(int(entry.get("dmx_min", 0)) <= raw <= int(entry.get("dmx_max", 255))
               for entry in ranges)


def water_level(levels, raw, profile):
    water = profile["water"]
    if water["role"] == "unknown":
        return 0.0
    pump, valve = water["pump_channel"], water["valve_channel"]
    level = levels.get(pump, 1.0 if valve in levels else 0.0)
    if valve in levels:
        level *= levels[valve]
    gate = water.get("gate_channel")
    if gate and not open_gate(raw.get(gate, 0), water.get("open_ranges", ())):
        return 0.0
    return level
