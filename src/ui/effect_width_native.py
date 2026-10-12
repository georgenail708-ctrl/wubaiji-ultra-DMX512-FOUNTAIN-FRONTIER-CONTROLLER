"""Width support for existing native fronts and cumulative chase fields.

Only the registered kernels use this policy. Full width retains their original
bytes; narrowed support stays inside the original field without moving its head.
"""
import math

from .effect_spatial_semantics import narrowed_params, width_factor


_CUMULATIVE = frozenset({"wipe", "build", "dissolve", "levelbar"})
_BASE_CHASES = frozenset({
    "EffectEngine._register_chase_presets.<locals>._single_chase",
    "EffectEngine._register_chase_presets.<locals>._dual_chase",
    "EffectEngine._register_chase_presets.<locals>._triple_chase",
})
_POLICY = "_effect_native_width_policy"
_MARKER = "_width_support_handled"


def register_native(fn):
    setattr(fn, _POLICY, True)
    return fn


def register_chase(fn, kind):
    if kind in _CUMULATIVE:
        register_native(fn)
    return fn


def is_base_chase(fn):
    return getattr(fn, "__qualname__", "") in _BASE_CHASES


def kernel_params(fn, params):
    """Native support consumes the existing parameter scaling once."""
    result = narrowed_params(params)
    if getattr(fn, _POLICY, False) or (is_base_chase(fn) and width_factor(params) < .999):
        return dict(result, **{_MARKER: True})
    return result


def _grid_step(params, n):
    if n <= 1:
        return 1.0
    direction = params.get("direction", "正向")
    step = 2.0 if direction in {"中心向外", "两侧向中"} else 1.0
    return step / (n - 1)


def _front_and_span(kind, sample, params):
    _pos, head, t, n = sample
    if kind == "levelbar":
        speed = float(params.get("speed", 1.0))
        head = 0.5 - 0.5 * math.cos(t * speed * 2 * math.pi)
    span = 1.0 - head if kind == "dissolve" else head
    support = max(span * width_factor(params), _grid_step(params, n))
    return head, support


def _cumulative_weight(kind, sample, params, weight):
    if not weight or width_factor(params) >= .999:
        return weight
    head, support = _front_and_span(kind, sample, params)
    pos = sample[0]
    if kind == "dissolve":
        return weight if pos <= head + support else 0.0
    return weight if pos >= head - support else 0.0


def chase_byte(kind, sample, params, values, clamp):
    """Retain the original range and quantization after narrowing filled support."""
    weight, lo, hi = values
    if kind in _CUMULATIVE:
        weight = _cumulative_weight(kind, sample, params, weight)
    return int(lo + clamp(weight) * (hi - lo))
