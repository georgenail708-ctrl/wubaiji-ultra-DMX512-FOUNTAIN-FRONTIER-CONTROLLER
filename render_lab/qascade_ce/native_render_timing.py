"""One owned CE scalar stage per original render/P7 control point."""
import ctypes as ct

from .native_binding import bind_native

I, D = ct.POINTER(ct.c_int), ct.POINTER(ct.c_double)
_INTEGERS, _NUMBERS = ct.c_int * 16, ct.c_double * 24
_FUNCTION = None
_STAGES = ("local_calls", "active_source_calls", "held_source_calls", "window_calls",
           "now_calls", "bucket_calls", "admission_calls",
           "cold_span_calls", "cold_bucket_calls", "index_elapsed_calls")
COUNTERS = dict.fromkeys(("timing_calls", "cadence_calls", "errors", "fallbacks", "cold_unsupported", *_STAGES), 0)


def install(engine):
    global _FUNCTION
    _FUNCTION = bind_native(engine.dll, "ce_render_timing_eval", [ct.c_int, I, D, D], ct.c_int)
    return available()


def available():
    return _FUNCTION is not None


def counters():
    return dict(COUNTERS)


def note_fallback():
    COUNTERS["fallbacks"] += 1


def invoke(operation, flags, values):
    if not available():
        raise RuntimeError("native render timing is not installed")
    integers, numbers, output = _INTEGERS(*flags), _NUMBERS(*values), _NUMBERS()
    status = _FUNCTION(operation, integers, numbers, output)
    if status:
        COUNTERS["errors"] += 1
        raise RuntimeError(f"native render timing failed: {status}")
    COUNTERS["timing_calls"] += 1
    COUNTERS[_STAGES[operation]] += 1
    return tuple(output)


def cadence(engine, position):
    from . import native_source_motion_clock as clock
    from .prepared_timeline import _cadence_inputs
    flags, values = _cadence_inputs(engine, position)
    result = float(clock.invoke_scalar(15, flags, values)[0])
    COUNTERS["cadence_calls"] += 1
    return result
