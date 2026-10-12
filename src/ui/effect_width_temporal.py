"""Role-specific width support around original fountain and time source frames.

Clock, source samples and per-group ordering remain owned by the existing
kernels. Uniform timing is retained; spatial fields use the proven sampler,
and discrete events retain their original values when thinning their density.
"""
import math

from . import effect_width_support as _width_support
from .effect_spatial_semantics import width_factor


def channel_range(params, default_channel="dimmer"):
    return (params.get("ch_type", default_channel),
            int(params.get("lo", 0)), int(params.get("hi", 255)))


def _uniform(role, params):
    family, kind = role
    if family == "fountain":
        return kind in {"burst", "crown"}
    return kind != "sh" and float(params.get("spread", 0.0)) == 0.0


def _point_values(values, normalized, width, lo):
    """Stratify retained original draws by rank without changing their bytes."""
    ordered = sorted(range(len(values)), key=lambda index: (-normalized[index], index))
    retained = {index for rank, index in enumerate(ordered)
                if rank == 0 or math.floor((rank + 1) * width) > math.floor(rank * width)}
    return [value if index in retained else lo for index, value in enumerate(values)]


def _smooth_values(normalized, width, interval):
    lo, hi = interval
    phases = [index / max(1, len(normalized) - 1) for index in range(len(normalized))]
    values = _width_support._compress(normalized, phases, width, False)
    return [max(min(lo, hi), min(max(lo, hi), lo + round(value * (hi - lo))))
            for value in values]


def _supported_values(role, values, width, interval):
    lo, hi = interval
    if hi == lo:
        return values
    normalized = [(value - lo) / (hi - lo) for value in values]
    family, kind = role
    if kind == "sh" or (family == "fountain" and kind == "cross"):
        return _point_values(values, normalized, width, lo)
    return _smooth_values(normalized, width, interval)


def finish(role, ef, params, sorted_fx):
    """Finish only this source's ordered channel, with no persistent state."""
    width = width_factor(params)
    if width >= .999 or _uniform(role, params):
        return
    default_channel = "pump" if role[0] == "fountain" else "dimmer"
    channel, lo, hi = channel_range(params, default_channel)
    indices = [orig_i for orig_i, _fi in sorted_fx]
    values = [ef.values[index][channel] for index in indices]
    supported = _supported_values(role, values, width, (lo, hi))
    for index, value in zip(indices, supported):
        ef.values[index][channel] = value
