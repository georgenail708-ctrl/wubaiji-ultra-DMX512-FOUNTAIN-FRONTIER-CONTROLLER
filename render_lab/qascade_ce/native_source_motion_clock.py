"""Native scalar source clocks; public caches keep their existing ownership."""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer

I, D = ct.POINTER(ct.c_int), ct.POINTER(ct.c_double)
_INT_INPUT, _DOUBLE_INPUT = ct.c_int * 16, ct.c_double * 24
_SCALAR_OPS = frozenset((0, 5, 6, 7, 9, 12, 14, 15, 16, 20, 25, 26, 27, 28, 29, 30, 31))

_FUNCTION = None
_INSTALLED_FUNCTION = None
_SOURCE_CLOCK_CALL_CNT = [0]
_SOURCE_CLOCK_SUCCESS_CNT = [0]
_SOURCE_CLOCK_ERROR_CNT = [0]
_SOURCE_CLOCK_FALLBACK_CNT = [0]
_SOURCE_CLOCK_CAP_CNT = [0]
_SOURCE_CLOCK_SCORE_SCALAR_CNT = [0]
_SOURCE_CLOCK_RANGE_POSITION_CNT = [0]
_SOURCE_CLOCK_TABLE_SCALAR_CNT = [0]
_SOURCE_CLOCK_REWIND_CNT = [0]
_SOURCE_CLOCK_CONSUMPTION_BOOST_CNT = [0]
_SOURCE_CLOCK_CACHE_INDEX_CNT = [0]
_SOURCE_CLOCK_CROSS_TRACK_CNT = [0]
MODES = {name: i for i, name in enumerate(("normal", "reverse", "pingpong", "loop", "once"))}


def install(engine):
    global _FUNCTION, _INSTALLED_FUNCTION
    _FUNCTION = bind_native(engine.dll, "ce_source_motion_clock", [ct.c_int, I, D, D, D, D], ct.c_int)
    _INSTALLED_FUNCTION = _FUNCTION
    return _FUNCTION is not None


def available():
    return _FUNCTION is not None


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_SOURCE_CLOCK_") and name.endswith("_CNT")}


def invoke(op, ints, values, pauses=()):
    if not available():
        _SOURCE_CLOCK_FALLBACK_CNT[0] += 1
        raise RuntimeError("native source clock is not installed")
    # These local zero-padded ABI inputs remain owned until synchronous C returns.
    integers, doubles = _INT_INPUT(*ints), _DOUBLE_INPUT(*values)
    pause = np.ascontiguousarray(pauses, dtype=np.float64)
    result, pause_out = np.empty(24, np.float64), np.empty(len(pause), np.float64)
    _SOURCE_CLOCK_CALL_CNT[0] += 1
    status = _FUNCTION(op, integers, doubles,
        borrowed_pointer(pause, D), borrowed_pointer(result, D), borrowed_pointer(pause_out, D))
    if status:
        _SOURCE_CLOCK_ERROR_CNT[0] += 1
        raise RuntimeError(f"native source clock failed: {status}")
    _SOURCE_CLOCK_SUCCESS_CNT[0] += 1
    return result, pause_out


def invoke_scalar(op, ints, values):
    if op not in _SCALAR_OPS:
        raise ValueError("operation requires array-bearing invoke")
    if not available():
        _SOURCE_CLOCK_FALLBACK_CNT[0] += 1
        raise RuntimeError("native source clock is not installed")
    # Fresh owners remain strong through C; every unused output slot starts zero.
    integers, doubles = _INT_INPUT(*ints), _DOUBLE_INPUT(*values)
    result = _DOUBLE_INPUT()
    _SOURCE_CLOCK_CALL_CNT[0] += 1
    status = _FUNCTION(op, integers, doubles, None, result, None)
    if status:
        _SOURCE_CLOCK_ERROR_CNT[0] += 1
        raise RuntimeError(f"native source clock failed: {status}")
    _SOURCE_CLOCK_SUCCESS_CNT[0] += 1
    if op == 31:
        _SOURCE_CLOCK_CROSS_TRACK_CNT[0] += 1
    return result


def _invoke_sample(op, ints, values, samples):
    if op not in (17, 18, 19):
        raise ValueError("operation requires a scalar sample result")
    if not available():
        _SOURCE_CLOCK_FALLBACK_CNT[0] += 1
        raise RuntimeError("native source clock is not installed")
    integers, doubles = _INT_INPUT(*ints), _DOUBLE_INPUT(*values)
    source = np.ascontiguousarray(samples, dtype=np.float64)
    result = _DOUBLE_INPUT()
    _SOURCE_CLOCK_CALL_CNT[0] += 1
    status = _FUNCTION(op, integers, doubles, borrowed_pointer(source, D), result, None)
    if status:
        _SOURCE_CLOCK_ERROR_CNT[0] += 1
        raise RuntimeError(f"native source clock failed: {status}")
    _SOURCE_CLOCK_SUCCESS_CNT[0] += 1
    return result


def _number(value, default=0.):
    try:
        return float(value or 0.)
    except (ValueError, TypeError):
        return default


def profile(engine, clip):
    key = engine._clip_cache_id(clip)
    cache = engine._clip_motor_force_profile_cache
    if key in cache:
        return cache[key]
    preset = str(getattr(clip, "motor_force_preset", "") or "").strip().lower()
    value = None
    if preset in {"gentle", "soft", "natural", "strong", "blast", "custom"}:
        try:
            parts = [float(getattr(clip, name, default) or 0.) for name, default in
                (("motor_force_accel", .2), ("motor_force_cruise", .6), ("motor_force_decel", .2))]
            value = tuple(invoke_scalar(0, [], parts)[:3])
        except (ValueError, TypeError):
            value = None
    cache[key] = value
    return value


def force_time(engine, clip, source_t, source_duration):
    parts = profile(engine, clip)
    result = invoke_scalar(7, [int(parts is not None)],
                    [source_t, source_duration, *(parts or (0., 0., 0.))])
    return float(result[0])


def playback_rate(clip):
    raw = getattr(clip, "playback_rate", 1.) or 1.
    rate = _number(raw, 1.)
    return float(invoke_scalar(6, [0], [0., rate, 0., 0.])[0])


def source_duration(clip, timeline_duration):
    declared = _number(getattr(clip, "source_duration", 0.))
    rate = _number(getattr(clip, "playback_rate", 1.) or 1., 1.)
    return float(invoke_scalar(6, [0], [timeline_duration, rate, declared, 0.])[1])


def phase_amount(engine, clip):
    parts = profile(engine, clip)
    manual = _number(getattr(clip, "motor_transition_phase_amount", 1.))
    force = _number(getattr(clip, "motor_force_phase_amount", 0.))
    flags = [int(bool(getattr(clip, "motor_transition_phase_enabled", False))), int(parts is not None)]
    return float(invoke_scalar(5, flags, [manual, force, 0., 0.])[0])


def window(clip, single, pauses, context):
    delay, transitional, batches, wing = (context[k] for k in ("delay", "fade", "batches", "wing"))
    values = [_number(getattr(clip, "duration", 0.)), single, delay, transitional]
    result, normalized = invoke(1, [batches, len(pauses), int(wing)], values, pauses)
    return float(result[0]), normalized.tolist(), None if result[1] < 0. else float(result[1])


def active_batch(local_t, single, pauses, context):
    batches, step = context["batches"], context["step"]
    result = invoke(2, [batches, len(pauses)], [local_t, single, step], pauses)[0]
    return int(result[0]), float(result[1]), bool(result[2])


def sequential_time(request):
    flags = [request[k] for k in ("enabled", "batches", "pause_count", "wing",
                                  "resize", "motor_no_delay", "cyclic")]
    values = [request[k] for k in ("local_t", "duration", "rate", "single", "step", "hold_end")]
    result = invoke(3, flags, values, request["pauses"])[0]
    return float(result[0]), bool(result[1])


def run_mode(request):
    flags = [request[k] for k in ("mode", "resize", "cycle_eligible")]
    values = [request[k] for k in ("source_t", "duration", "recorded_span", "first", "period", "last")]
    result = invoke(4, flags, values)[0]
    return float(result[0]), bool(result[1])


def extent(clip, cycle):
    values = [_number(getattr(clip, "duration", 0.)),
              _number(getattr(clip, "playback_rate", 1.) or 1., 1.),
              _number(getattr(clip, "source_duration", 0.)), cycle[2] if cycle else 0.]
    return bool(invoke_scalar(6, [int(cycle is not None)], values)[2])


def hold_cap(clip, clip_start):
    duration = float(getattr(clip, "duration", 0.) or 0.)
    result = float(invoke_scalar(12, [], [clip_start, duration])[0])
    _SOURCE_CLOCK_CAP_CNT[0] += 1
    return result


def score_scalars(payload):
    flags = [int(payload["hold"]), int(payload["index_valid"])]
    values = [float(payload[key]) for key in
              ("rate", "start", "duration", "priority", "track_order", "total_priority")]
    result = invoke_scalar(14, flags, values)
    _SOURCE_CLOCK_SCORE_SCALAR_CNT[0] += 1
    return dict(zip(("rate", "hold_cap", "tie", "preboost"), map(float, result[:4]), strict=True))


def range_position(index, interval, end):
    result = invoke_scalar(26, [], [float(index), float(interval), float(end)])
    _SOURCE_CLOCK_RANGE_POSITION_CNT[0] += 1
    return float(result[0])


def table_scalars(payload):
    flags = [int(payload[key]) for key in ("hold", "input_direction", "output_direction")]
    values = [float(payload[key]) for key in
              ("priority", "track_order", "start", "local", "duration", "fade_in",
               "fade_out", "delay_in", "delay_out", "total_priority")]
    result = invoke_scalar(27, flags, values)
    _SOURCE_CLOCK_TABLE_SCALAR_CNT[0] += 1
    numbers = dict(zip(("base_tie", "boost", "tie", "duration", "fade_in", "fade_out",
                        "delay_in", "delay_out"), map(float, result[:8]), strict=True))
    numbers.update(zip(("window_in", "window_out", "window_delay", "real_hold", "fast_ok"),
                       map(bool, result[8:13]), strict=True))
    return numbers


def rewind_needed(now, previous):
    if previous is None:
        return False
    result = invoke_scalar(28, [1], [float(now), float(previous)])
    _SOURCE_CLOCK_REWIND_CNT[0] += 1
    return bool(result[0])


def consumption_boost(count):
    result = invoke_scalar(29, [], [float(count)])
    _SOURCE_CLOCK_CONSUMPTION_BOOST_CNT[0] += 1
    return float(result[0])


def cache_index(interval, end, now):
    result = invoke_scalar(30, [], [float(interval), float(end), float(now)])
    _SOURCE_CLOCK_CACHE_INDEX_CNT[0] += 1
    if result[4] == 1.:
        raise ValueError("cannot convert float NaN to integer")
    if result[4] == 2.:
        raise OverflowError("cannot convert float infinity to integer")
    return int(result[0])


def sample_endpoint(duration):
    return float(invoke_scalar(16, [], [float(duration)])[0])


def sample_floor(timestamps, source_t):
    times = np.asarray(timestamps, dtype=np.float64)
    if times.ndim != 1:
        raise ValueError("object too deep for desired array")
    return int(_invoke_sample(17, [times.size], [float(source_t)], times)[0])


def sample_nearest(timestamps, source_t):
    times = np.asarray(timestamps, dtype=np.float64)
    if times.size <= 1:
        return int(_invoke_sample(18, [times.size], [0.], times.reshape(-1))[0])
    if times.ndim != 1:
        raise ValueError("object too deep for desired array")
    return int(_invoke_sample(18, [times.size], [float(source_t)], times)[0])


def auto_hold(timestamps, position):
    return float(_invoke_sample(19, [len(timestamps)], [float(position)], timestamps)[0])


def transition_scalar(payload):
    flags = [payload[key] for key in ("enabled", "curve", "strategy", "fade_in")]
    return invoke_scalar(20, flags, [float(payload["progress"])])


def fade_report(payload):
    flags = [payload[key] for key in
             ("enabled", "curve_in", "curve_out", "strategy_in", "strategy_out")]
    values = [float(payload[key]) for key in ("duration", "fade_in", "fade_out", "local_t")]
    return invoke_scalar(25, flags, values)


def _vector_buffers(payload):
    progress = np.asarray(payload["progress"], dtype=np.float32)
    lut = payload.get("lut")
    custom = not payload["enabled"] and lut is not None
    if not custom:
        return progress, progress.reshape(-1).astype(np.float64), (0, 1), progress.shape
    lut = np.asarray(lut, dtype=np.float32)
    rows = len(lut)
    width = lut.size // rows if rows else 1
    joined = np.concatenate((progress.reshape(-1).astype(np.float64),
                             lut.reshape(-1).astype(np.float64)))
    required = progress.size * width
    if required > joined.size:
        joined = np.concatenate((joined, np.zeros(required - joined.size, np.float64)))
    return progress, joined, (rows, width), progress.shape + lut.shape[1:]


def transition_vector(payload):
    progress, joined, dimensions, shape = _vector_buffers(payload)
    rows, width = dimensions
    custom = not payload["enabled"] and payload.get("lut") is not None
    flags = [progress.size, payload["enabled"], payload["curve"], payload["strategy"],
             payload["fade_in"], int(custom), rows, width]
    report, values = invoke(21, flags, [], joined)
    if report[1]:
        return None, (int(report[2]), rows)
    result = values[:progress.size * width].astype(np.float32).reshape(shape)
    return result, None
