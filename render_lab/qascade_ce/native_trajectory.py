"""Trajectory facade bound once to the mounted CE DLL; actual report counters."""
import ctypes as ct

from .native_binding import bind_native
from .native_trajectory_types import Report, Request, WORK

_FUNCTION = None
_ENGINE = None
_CONTEXT_TYPE = None
_CONTEXT_METHODS = ()
_BUILTINS = ()
_COUNTERS = dict.fromkeys(("call", "success", "errors", "fallbacks",
                         "unsupported", "override") + WORK, 0)


def install(engine):
    global _FUNCTION, _ENGINE, _CONTEXT_TYPE, _CONTEXT_METHODS, _BUILTINS
    from src.processing.dispatch import trajectory_replay as replay
    function = bind_native(engine.dll, "ce_trajectory_eval",
                           [ct.c_int, ct.POINTER(Request), ct.POINTER(Report)],
                           ct.c_int)
    if function is None:
        raise RuntimeError("Mounted CE is missing ce_trajectory_eval")
    _FUNCTION, _ENGINE = function, engine
    _CONTEXT_TYPE = replay.ReplayContext
    _CONTEXT_METHODS = (replay.ReplayContext.offsets_for,
                        replay.ReplayContext.offsets_by_fixture)
    _BUILTINS = (replay._apply_strip, replay._apply_range, replay._apply_master)
    return True


def available():
    return _FUNCTION is not None


def counters():
    return dict(_COUNTERS)


def accepts_sample(seq, position):
    """Foreign numeric/operator subclasses retain their explicit extension path."""
    if not seq:
        return True
    ordinary = type(position) is float
    for time, value in seq:
        ordinary = ordinary and type(time) is float
        if isinstance(value, (int, float)):
            ordinary = ordinary and type(value) in (int, float, bool)
    if not ordinary:
        _COUNTERS["unsupported"] += 1
    return ordinary


def _ordinary_context(ctx):
    if ctx is None:
        return True
    return (type(ctx) is _CONTEXT_TYPE
            and getattr(ctx.offsets_for, "__func__", None) is _CONTEXT_METHODS[0]
            and getattr(ctx.offsets_by_fixture, "__func__", None) is _CONTEXT_METHODS[1])


def accepts_value(value, kind, ctx=None):
    if kind == "range" and type(value) in (tuple, list):
        sources = value[1:]
        mode_ordinary = not value or type(value[0]) in (int, float, bool, str, type(None))
    else:
        sources = (value,)
        mode_ordinary = True
    ordinary = all(type(source) in (int, float, bool, str, type(None))
                   for source in sources)
    ordinary = ordinary and mode_ordinary and _ordinary_context(ctx)
    if not ordinary:
        _COUNTERS["unsupported"] += 1
    return ordinary


def registered_builtin(applier):
    if applier in _BUILTINS:
        return True
    _COUNTERS["unsupported"] += 1
    return False


def _ordinary_controls(clip):
    from src.processing.dispatch.trajectory_replay import CONTROL_APPLIERS
    for key in (getattr(clip, "trajectories", None) or {}):
        if type(key) is not str:
            return False
        parts = key.split("|")
        applier = CONTROL_APPLIERS.get("|".join(parts[:2]))
        if applier is not None and applier not in _BUILTINS:
            return False
    return True


def accepts_clips(clips):
    ordinary = all(_ordinary_controls(clip) for clip in clips)
    if not ordinary:
        _COUNTERS["unsupported"] += 1
    return ordinary


def evaluate(operation, payload, owners):
    """Strong local owners span the synchronous call; no retained native borrow."""
    if _FUNCTION is None:
        raise RuntimeError("Trajectory CE is not installed")
    report = Report()
    _COUNTERS["call"] += 1
    status = _FUNCTION(operation, ct.byref(payload), ct.byref(report))
    # Retain owners through return, including caller-owned output arrays.
    if status or report.status:
        _COUNTERS["errors"] += 1
        raise RuntimeError(f"Trajectory CE rejected ABI: {status}/{report.status}")
    _COUNTERS["success"] += 1
    for name in WORK:
        _COUNTERS[name] += int(getattr(report, name))
    return report


def sample(seq, position):
    from .native_trajectory_sampling import sample
    return sample(seq, position)


def apply(ctx, channel, value, destination, kind):
    from .native_trajectory_appliers import apply
    return apply(ctx, channel, value, destination, kind)


def build(clip, source_t, mappings, released):
    from .native_trajectory_appliers import build
    return build(clip, source_t, mappings, released)


def span(clip, position):
    from .native_trajectory_merge import time_value
    return time_value(clip, position, 1)


def source(clip, position):
    from .native_trajectory_merge import time_value
    return time_value(clip, position, 2)


def combine(frames):
    from .native_trajectory_merge import combine
    return combine(frames)


def merge(outputs, combined):
    from .native_trajectory_merge import merge
    return merge(outputs, combined)
