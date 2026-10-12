"""Shrink the support of the existing wave/scalar samples without repeating peaks.

Full width returns to the original kernel. Narrow width keeps the original
sampled peaks, low value, phase progression, deterministic source and targets.
Each shape/unit call is independent; no per-show or per-frame state is stored.
"""
import math

from .effect_spatial_semantics import narrowed_params, width_factor
from . import effect_width_native as _native_width


_FAMILIES = ("_scalar_kernel.<locals>.fn",
             "_register_wave_presets.<locals>.make_fn.<locals>.fn",
             "_envelope_kernel.<locals>.fn", "_breathe_kernel.<locals>.fn",
             "_fountain_kernel.<locals>.fn")
_MARKER = "_width_support_handled"
_NOISE = {"Random", "S&H", "Wander"}


def kernel_params(fn, params):
    if width_factor(params) >= .999:
        return narrowed_params(params)
    if fn.__qualname__.endswith(_FAMILIES):
        return dict(params, **{_MARKER: True})
    return _native_width.kernel_params(fn, params)


def profile_handled(params):
    return bool(params.get(_MARKER))


def _peaks(values):
    q = [int(value * 255) for value in values]
    starts = [i for i in range(len(q)) if q[i] != q[i - 1]]
    if not starts:
        return q, []
    peaks = []
    ends = starts[1:] + [starts[0] + len(q)]
    for start, end in zip(starts, ends):
        if q[start] > q[start - 1] and q[start] > q[end % len(q)]:
            center = ((start + end - 1) / 2) % len(q)
            peaks.append((center, (end - start - 1) / 2))
    return q, sorted(peaks)


def _valley(q, left, right):
    indices = range(math.ceil(left), math.floor(right) + 1)
    minimum = min(q[index % len(q)] for index in indices)
    bottom = [index for index in indices if q[index % len(q)] == minimum]
    return (bottom[0] + bottom[-1]) / 2


def _sample(values, location):
    left = math.floor(location)
    fraction = location - left
    a, b = values[left % len(values)], values[(left + 1) % len(values)]
    return a + (b - a) * fraction


def _peak_value(values, center, half):
    return max(values[index % len(values)]
               for index in range(math.ceil(center - half), math.floor(center + half) + 1))


def _compressed_cell(values, output, cell, width):
    center, half, left, right = cell
    core = max(half * width, min(half, .5))
    old_left, old_right = center - half, center + half
    new_left, new_right = center - core, center + core
    left_tail, right_tail = old_left - left, right - old_right
    start, end = new_left - left_tail * width, new_right + right_tail * width
    peak = _peak_value(values, center, half)
    for index in range(math.ceil(start), math.floor(end) + 1):
        value = _cell_value(values, index, (old_left, old_right, new_left, new_right),
                            width=width, peak=peak)
        output[index % len(values)] = value


def _cell_value(values, index, edges, *, width, peak):
    old_left, old_right, new_left, new_right = edges
    if index < new_left:
        return _sample(values, old_left + (index - new_left) / width)
    if index > new_right:
        return _sample(values, old_right + (index - new_right) / width)
    return peak


def _cells(q, peaks):
    n = len(q)
    for i, (center, half) in enumerate(peaks):
        previous = peaks[i - 1][0]
        following = peaks[(i + 1) % len(peaks)][0]
        if previous >= center:
            previous -= n
        if following <= center:
            following += n
        yield center, half, _valley(q, previous, center), _valley(q, center, following)


def _phase_window(values, phases, width):
    """Keep original noise samples/hold/interpolation; narrow their occupied window."""
    half = width / 2
    return [value if abs(phase % 1 - .5) <= half else 0.0
            for value, phase in zip(values, phases)]


def _compress(values, phases, width, noise):
    if len(values) < 4:
        return values
    if noise:
        return _phase_window(values, phases, width)
    q, peaks = _peaks(values)
    if not peaks:
        return _phase_window(values, phases, width)
    output = [0.0] * len(values)
    for cell in _cells(q, peaks):
        _compressed_cell(values, output, cell, width)
    return output


def _lookup_sampler(values, phases, params, *, noise=False):
    values = _compress(values, phases, width_factor(params), noise)
    lookup = dict(zip(phases, values))
    return lookup.__getitem__


def _generic_phases(params, t, sorted_fx):
    from .effect_engine import spatial_phase_index
    speed = float(params.get("speed", 1.0))
    step = float(params.get("phase_step", .1))
    n = len(sorted_fx)
    return [t * speed + spatial_phase_index(params, str(fi.id), i, n) * step
            for i, (_orig, fi) in enumerate(sorted_fx)]


def generic_sampler(name, params, t, sorted_fx, functions):
    fn = functions.get(name, functions["Sine"])
    if width_factor(params) >= .999:
        return fn
    phases = _generic_phases(params, t, sorted_fx)
    values = [fn(phase) for phase in phases]
    return _lookup_sampler(values, phases, params, noise=name in _NOISE)


def scalar_sampler(shape, params, t, n, fn):
    if width_factor(params) >= .999:
        return None
    speed = float(params.get("speed", 1.0))
    frequency = float(params.get("spatial_freq", 1.0))
    duty = float(params.get("duty", .5))
    phases = [i / max(1, n - 1) * frequency - t * speed for i in range(n)]
    values = [fn(shape, phase, duty) for phase in phases]
    return _lookup_sampler(values, phases, params)


def scalar_at(sampler, shape, phase, duty, fn):
    return fn(shape, phase, duty) if sampler is None else sampler(phase)
