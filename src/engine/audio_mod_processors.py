"""AM21 stateless processing of existing Clip frames before original safety/mix.

Caller owns strict folder/Clip membership and supplies one feature frame context.
No capture, Clip generation, mutable frame history, cache or transport. Original
coarse/fine axis APIs and shared HSV color conversion are reused directly.
"""
from __future__ import annotations

import colorsys
import math
import numpy as np

from src.utils.color_math import hsv_to_rgb_float
from .xy_reference import read_axis_logical_value, write_axis_logical_value

_LEVEL_TYPES = frozenset(("r", "g", "b", "w", "dimmer", "pump"))


def _unit(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return 0.0
    return min(1., max(0., number)) if math.isfinite(number) else 0.0


def _addresses(plan, targets, allowed):
    channels = plan.get("channels", {})
    return tuple(sorted({address for kind in targets if kind in allowed
                         for address in channels.get(kind, ())}))


def _scale_levels(data, active, addresses, scale):
    if not addresses or scale >= 1.0:
        return data
    indexes = np.asarray(addresses, dtype=np.intp)
    indexes = indexes[np.asarray(active, dtype=bool)[indexes]]
    if indexes.size == 0:
        return data
    values = data[indexes] * scale
    if np.array_equal(values, data[indexes]):
        return data
    result = data.copy()
    result[indexes] = values
    return result


def _slice_window(row, features):
    params = row["params"]
    phase = (_unit(features.get("beat_phase")) * params["division"] + params["phase"]) % 1.
    duty = params["duty"]
    if phase >= duty:
        return 0.0
    edge = min(params["edge"], duty * .5)
    if edge <= 0.:
        return 1.0
    return min(1., phase / edge, (duty - phase) / edge)


def _slice(data, active, row, plan, features):
    gate = _slice_window(row, features)
    floor = row["params"]["floor"]
    control = _unit(features.get(row["source"]))
    factor = floor + (1. - floor) * gate * control
    scale = 1. - row["wet"] + row["wet"] * factor
    addresses = _addresses(plan, row["targets"], _LEVEL_TYPES)
    result = _scale_levels(data, active, addresses, scale)
    valves = _addresses(plan, row["targets"], frozenset(("valve",)))
    return _gate_valves(data, result, active, valves,
                        gate_open=(factor >= .5 or row["wet"] < .5))


def _gate_valves(data, result, active, valves, *, gate_open):
    # Valves are discrete: retain the original On byte or use exact Off 0.
    # Wet bypass is handled by the dispatcher; intermediate wet never invents mode bytes.
    if gate_open:
        return result
    indexes = [address for address in valves if active[address] and data[address] != 0]
    if not indexes:
        return result
    if result is data:
        result = data.copy()
    result[indexes] = 0.
    return result


def _accent(data, active, row, plan, features):
    control = _unit(features.get(row["source"])) ** row["params"]["curve"]
    if row["params"]["mode"] == "duck":
        control = 1. - control
    floor = row["params"]["floor"]
    factor = floor + (1. - floor) * control
    scale = 1. - row["wet"] + row["wet"] * factor
    return _scale_levels(data, active, _addresses(plan, row["targets"], _LEVEL_TYPES), scale)


def _axis_selected(axis, active, targets):
    if axis["type"] not in targets or not active[axis["coarse"]]:
        return False
    return True if axis["fine"] is None else bool(active[axis["fine"]])


def _motion_axis(data, result, axis, scale):
    coarse, fine = axis["coarse"], axis["fine"]
    logical = read_axis_logical_value(data, coarse, fine)
    value = axis["home"] + (logical - axis["home"]) * scale
    # Original writer clamps >=255 to FFFF: identity must never pass through it.
    if abs(value - logical) < 1e-12:
        return result
    scratch = np.asarray((data[coarse], 0. if fine is None else data[fine]), dtype=np.float32)
    write_axis_logical_value(scratch, 0, None if fine is None else 1, value)
    indexes = (coarse,) if fine is None else (coarse, fine)
    values = scratch[:len(indexes)]
    if np.array_equal(values, data[list(indexes)]):
        return result
    if result is data:
        result = data.copy()
    result[list(indexes)] = values
    return result


def _motion(data, active, row, plan, features):
    params = row["params"]
    control = _unit(features.get(row["source"]))
    amount = params["min_scale"] + (params["max_scale"] - params["min_scale"]) * control
    scale = 1. - row["wet"] + row["wet"] * amount
    if scale >= 1.:
        return data
    result = data
    for axis in plan.get("axes", ()):
        if _axis_selected(axis, active, row["targets"]):
            result = _motion_axis(data, result, axis, scale)
    return result


def _color_group(data, indexes, offset, wet):
    rgb = np.asarray(data[list(indexes)], dtype=np.float32)
    peak = float(rgb.max())
    if peak <= 0.:
        return rgb
    hue, saturation, value = colorsys.rgb_to_hsv(*(rgb / 255.))
    shifted = np.asarray(hsv_to_rgb_float((hue + offset) % 1., saturation, value)) * 255.
    mixed = rgb * (1. - wet) + shifted * wet
    return (mixed * (peak / float(mixed.max()))).astype(np.float32)


def _color(data, active, row, plan, features):
    if not {"r", "g", "b"}.issubset(row["targets"]):
        return data
    offset = row["params"]["hue_deg"] / 360. * _unit(features.get(row["source"]))
    if abs(offset) < 1e-12:
        return data
    result = data
    for indexes in plan.get("rgb", ()):
        if not np.all(np.asarray(active)[list(indexes)]):
            continue
        values = _color_group(data, indexes, offset, row["wet"])
        if np.array_equal(values, data[list(indexes)]):
            continue
        if result is data:
            result = data.copy()
        result[list(indexes)] = values
    return result


_PROCESSORS = {"slice": _slice, "accent": _accent, "motion": _motion, "color": _color}


def _apply_processors_python(data, active, rows, plan, feature_view):
    """Process normalized rows in order; caller data/active remain unchanged.

    Non-target/inactive/function bytes are retained. Unavailable/silent audio,
    all bypass/wet0 and unchanged results preserve the original data object.
    Motion cannot expand the existing offset; original Clip safety follows this.
    """
    if not feature_view.get("ready", False) or not feature_view.get("has_audio", False):
        return data
    if not plan:
        return data
    result = data
    for row in rows:
        if not row["enabled"] or row["wet"] <= 0.:
            continue
        processor = _PROCESSORS.get(row["kind"])
        if processor is not None:
            result = processor(result, active, row, plan, feature_view)
    return result


def apply_processors(data, active, rows, plan, feature_view):
    """Use installed native math; preserve the original unmounted oracle path."""
    from .native_audio_bridge import binding
    native = binding()
    if native is not None:
        return native.process(data, active, rows, plan, feature_view)
    return _apply_processors_python(data, active, rows, plan, feature_view)
