"""Owned native inputs for retained direction-row scalar/vector preparation."""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer

I, D = ct.POINTER(ct.c_int), ct.POINTER(ct.c_double)
F, U = ct.POINTER(ct.c_float), ct.POINTER(ct.c_uint8)
_INTS, _DOUBLES = ct.c_int*16, ct.c_double*24
_FUNCTION = None
_LEGACY_DIRECTION_CALL_CNT = [0]
_LEGACY_DIRECTION_NATIVE_SUCCESS_CNT = [0]


def install(engine):
    global _FUNCTION
    _FUNCTION = bind_native(engine.dll, "ce_legacy_direction_prepare",
        [ct.c_int, I, D, D, F, D, U, F], ct.c_int)
    return _FUNCTION is not None


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_LEGACY_DIRECTION_") and name.endswith("_CNT")}


def invoke(payload):
    if _FUNCTION is None:
        raise RuntimeError("retained direction preparation is not installed")
    values = np.ascontiguousarray(payload.get("values", ()), np.float64)
    vector = np.ascontiguousarray(payload.get("vector", ()), np.float32)
    integers = [len(values), len(vector), *payload.get("ints", ())]
    if any(not -(2**31) <= value < 2**31 for value in integers):
        raise ValueError("retained direction metadata outside native int range")
    ints, doubles = _INTS(*integers), _DOUBLES(*payload.get("scalars", ()))
    result = _DOUBLES()
    flags, output = np.empty(len(values), np.uint8), np.empty(len(vector), np.float32)
    _LEGACY_DIRECTION_CALL_CNT[0] += 1
    status = _FUNCTION(payload["op"], ints, doubles, borrowed_pointer(values, D),
        borrowed_pointer(vector, F), result, borrowed_pointer(flags, U), borrowed_pointer(output, F))
    if status:
        raise RuntimeError(f"retained direction preparation failed: {status}")
    _LEGACY_DIRECTION_NATIVE_SUCCESS_CNT[0] += 1
    return result, flags, output


def duration(value):
    result = invoke(dict(op=0, scalars=[float(value)], ints=[1, 0]))[0]
    return float(result[0]), bool(result[17])


def core(clip, pauses, mode):
    names = ("duration", "fade_in", "fade_out", "seq_delay_in", "seq_delay_out",
             "fade_group_single_duration", "fade_group_pause")
    scalars = [float(getattr(clip, name, 0.) or 0.) for name in names]
    groups = int(getattr(clip, "fade_groups", 1) or 1)
    return invoke(dict(op=0, scalars=scalars, ints=[groups, int(mode == "sequential")],
                       values=pauses))[0]


def fixture(row, batches, pauses):
    keys = ("single_raw", "sdi", "sdo", "fi", "fo", "dur")
    result = invoke(dict(op=1, scalars=[row[key] for key in keys],
                        ints=[0, batches], values=pauses))[0]
    names = ("per_group", "out_target", "out_span", "out_scale", "out_per_group")
    return dict(zip(names, map(float, result[:5])))


def scale_offsets(vector, scale):
    return invoke(dict(op=2, scalars=[scale], vector=vector))[2]


def ratio_flags(values):
    return invoke(dict(op=3, values=values))[1]


def ratio_nondefault(values):
    return bool(invoke(dict(op=3, values=values))[0][0])


def group_counts(count, groups):
    result = invoke(dict(op=4, ints=[int(count), int(groups)]))[0]
    return int(result[0]), int(result[1])


def minimum(left, right):
    first, second = np.asarray(left, np.float32), np.asarray(right, np.float32)
    if first.ndim != 1 or second.shape != first.shape:
        raise ValueError("retained edge gains must have matching one-dimensional shapes")
    packed = np.concatenate((first, second))
    return invoke(dict(op=5, vector=packed))[2][:len(first)]
