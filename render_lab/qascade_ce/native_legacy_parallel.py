"""Retained P5a metadata bridge for R16 curves and independent fade edges."""
import functools
import inspect

import numpy as np

from . import native_legacy_direction_prepare as prepare
from .native_binding import borrowed_pointer
from .native_source_motion_types import CURVES, STRATEGIES, F

_LEGACY_LIBRARY_CALL_CNT = [0]
_LEGACY_LIBRARY_SUCCESS_CNT = [0]
_LEGACY_LIBRARY_PUBLIC_CNT = [0]
_LEGACY_LIBRARY_ERROR_CNT = [0]
_LEGACY_LIBRARY_FALLBACK_CNT = [0]
SCALARS = ("fade_in", "fade_out", "duration", "local_t")
WINGS = ("seq_delay_in", "seq_delay_out", "fade_in_scale", "fade_out_scale")


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_LEGACY_LIBRARY_") and name.endswith("_CNT")}


def _codes(owner, clip):
    if not owner._clip_transition_enabled(clip):
        return ((3, 0), (3, 0))
    return tuple((CURVES[owner._clip_transition_curve(clip, fade_in=edge)],
                  STRATEGIES[owner._clip_transition_strategy(clip, fade_in=edge)])
                 for edge in (True, False))


def _special(codes):
    return codes[0][0] >= 100 or codes[1][0] >= 100 or codes[0] != codes[1]


def _arrays(owner, request):
    arrays = []
    for edge, flag in (("in", "is_fading_in"), ("out", "is_fading_out")):
        if not request[flag]:
            arrays.extend((None, None))
            continue
        group = owner._sequential_fixture_group_data(request["clip"],
            request["local_fixtures"], request[edge+"_direction"], request["groups"],
            track=request["track"], proj=request["proj"])
        arrays.extend(np.ascontiguousarray(value, np.float32) for value in group[1:3])
    return arrays


def _pointer(array):
    return None if array is None else borrowed_pointer(array, F)


def _call(engine, request, arrays, stage):
    from . import _CE_EXC_CNT
    flags, codes = stage
    output = np.empty(len(request["local_fixtures"] or []), np.float32)
    _LEGACY_LIBRARY_CALL_CNT[0] += 1
    try:
        engine.ce_parallel_fixture_gains(*(_pointer(value) for value in arrays),
            len(output), *(float(request[name]) for name in SCALARS),
            *flags, *(float(request[name]) for name in WINGS), *codes,
            borrowed_pointer(output, F))
    except Exception:
        _CE_EXC_CNT[0] += 1
        raise
    _LEGACY_LIBRARY_SUCCESS_CNT[0] += 1
    return output


def _plan(request, codes):
    flags = tuple(int(bool(request[name])) for name in ("is_fading_in", "is_fading_out"))
    if flags == (1, 1) and codes[0] != codes[1]:
        return (((1, 0), codes[0]), ((0, 1), codes[1]))
    return ((flags, codes[1] if flags == (0, 1) else codes[0]),)


def _hook_count(owner):
    counts = getattr(owner, "_ce_hook_counts", None)
    if counts is None:
        counts = {"p5a": 0, "p5b": 0}
        owner._ce_hook_counts = counts
    counts["p5a"] = counts.get("p5a", 0)+1


def _native(owner, engine, request, codes):
    from . import _ce_gain_arrays_ok
    if getattr(engine, "ce_parallel_fixture_gains", None) is None or request["ease_lut"] is not None:
        return None
    arrays = _arrays(owner, request)
    if not _ce_gain_arrays_ok(len(request["local_fixtures"] or []), *arrays):
        return None
    _hook_count(owner)
    results = [_call(engine, request, arrays, stage) for stage in _plan(request, codes)]
    result = results[0] if len(results) == 1 else prepare.minimum(*results)
    _LEGACY_LIBRARY_PUBLIC_CNT[0] += 1
    return result


def wrap_parallel_library(original, engine):
    signature = inspect.signature(original)

    @functools.wraps(original)
    def invoke(owner, *args, **kwargs):
        bound = signature.bind(owner, *args, **kwargs)
        bound.apply_defaults()
        request = bound.arguments
        codes = _codes(owner, request["clip"])
        if not _special(codes):
            return original(owner, *args, **kwargs)
        try:
            result = _native(owner, engine, request, codes)
        except Exception:
            _LEGACY_LIBRARY_ERROR_CNT[0] += 1
            raise
        if result is not None:
            return result
        _LEGACY_LIBRARY_FALLBACK_CNT[0] += 1
        return original(owner, *args, **kwargs)

    return invoke
