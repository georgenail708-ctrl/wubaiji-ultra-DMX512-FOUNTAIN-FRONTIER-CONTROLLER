"""Finish original scalar carriers and coherent color rows at their logical units."""
import math

from .effect_spatial_semantics import width_factor
from . import effect_width_support as _support
from .effect_width_capabilities import COLORS, MOTORS


def _interval(params):
    return (float(params.get("lo", params.get("min", params.get("min_val", 0)))),
            float(params.get("hi", params.get("max", params.get("max_val", 255)))))


def _driver(rows):
    keys = set().union(*(row.keys() for row in rows))
    scalar = keys - COLORS - MOTORS
    return scalar if scalar else keys & COLORS


def _point_support(values, width):
    ordered = sorted(range(len(values)), key=lambda index: (-values[index], index))
    retained = {index for rank, index in enumerate(ordered)
                if rank == 0 or math.floor((rank + 1) * width) > math.floor(rank * width)}
    return [value if index in retained else 0.0 for index, value in enumerate(values)]


def _support_values(values, params):
    width = width_factor(params)
    if params.get("_typed_width_point"):
        return _point_support(values, width)
    phases = [index / max(1, len(values) - 1) for index in range(len(values))]
    compressed = _support._compress(values, phases, width, False)
    return [min(before, after) for before, after in zip(values, compressed)]


def _supported_driver(values, params, interval):
    lo, hi = interval
    normalized = [(value - lo) / (hi - lo) for value in values]
    trough = params.get("_typed_width_kind") in {"trough", "notch"}
    field = [1 - value for value in normalized] if trough else normalized
    supported = _support_values(field, params)
    mapped = [1 - value for value in supported] if trough else supported
    return [lo + (hi - lo) * value for value in mapped]


def _write_row(row, before, after, lo, driver):
    scalar_driver = bool(driver - COLORS)
    origin = lo if scalar_driver else 0.0
    gain = (after - origin) / (before - origin) if before != origin else 1.0
    out = dict(row)
    for key, value in row.items():
        if key in MOTORS:
            continue
        baseline = 0.0 if key in COLORS else lo
        out[key] = max(0, min(255, round(baseline + (value - baseline) * gain)))
    return out


def finish(rows, params):
    if not params.get("_typed_width") or width_factor(params) >= .999 or len(rows) < 4:
        return rows
    driver = _driver(rows)
    if not driver:
        return rows
    values = [max((row.get(key, 0) for key in driver), default=0) for row in rows]
    interval = _interval(params) if driver - COLORS else (0.0, 255.0)
    if max(values) - min(values) <= 1 or interval[0] == interval[1]:
        return rows
    supported = _supported_driver(values, params, interval)
    return [_write_row(row, before, after, interval[0], driver)
            for row, before, after in zip(rows, values, supported)]


def motor_envelope(values, params):
    if width_factor(params) >= .999:
        return values
    phases = [index / max(1, len(values) - 1) for index in range(len(values))]
    compressed = _support._compress(values, phases, width_factor(params), False)
    return [min(before, after) for before, after in zip(values, compressed)]


def breathing_envelope(ctx, functions):
    t, n, params = ctx
    group_size = int(params.get("group_size", 3))
    speed = float(params.get("breath_speed", .3))
    degrees = float(params.get("breath_phase_deg", 60))
    group_of, sine = functions
    values = [.5 + .5 * sine(t * speed + degrees / 360 * group_of(float(i), group_size)[0])
              for i in range(n)]
    return motor_envelope(values, params)
