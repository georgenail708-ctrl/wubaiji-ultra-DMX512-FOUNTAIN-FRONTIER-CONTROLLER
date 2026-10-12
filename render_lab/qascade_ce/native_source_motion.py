"""One installed CE source domain; metadata marshaling has no channel math."""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer
from .native_source_motion_types import (CURVES, STRATEGIES, CopyRow, D, DirectionSpec,
    F, RemapSpec, SourceTable, U, direction_spec, pointer, remap_spec, source_table)

_FUNCTIONS = None
_SOURCE_MOTION_CALL_CNT = [0]
_SOURCE_MOTION_SUCCESS_CNT = [0]
_SOURCE_MOTION_DIRECTION_CNT = [0]
_SOURCE_MOTION_FORCE_CNT = [0]
_SOURCE_MOTION_REMAP_CNT = [0]
_SOURCE_MOTION_SCORE_CNT = [0]
_SOURCE_MOTION_FADE_CNT = [0]
_SOURCE_MOTION_POSITION_CNT = [0]
_SOURCE_MOTION_ERROR_CNT = [0]
_SOURCE_MOTION_FALLBACK_CNT = [0]
SIGNATURES = {
    "abi": ("ce_source_motion_abi", [], ct.c_int),
    "size": ("ce_source_motion_direction_size", [], ct.c_int),
    "direction": ("ce_source_motion_direction", [ct.POINTER(DirectionSpec), F, U, U], ct.c_int),
    "fade": ("ce_source_motion_fade", [ct.c_double]*4+[ct.c_int]*5, ct.c_double),
    "force": ("ce_source_motion_force", [D, U, ct.c_int]+[ct.c_double]*5+[U, U, U, D], ct.c_int),
    "scores": ("ce_source_motion_scores", [D, U, ct.c_int]+[ct.c_double]*4+[U, D], ct.c_int),
    "remap": ("ce_source_motion_remap", [ct.POINTER(SourceTable), ct.c_int,
        ct.POINTER(CopyRow), ct.c_int, ct.POINTER(RemapSpec), U, U], ct.c_int),
    "groups": ("ce_source_motion_groups", [F, ct.c_int, ct.c_int, ct.c_int, F, F, F], ct.c_int),
    "scores_row": ("ce_source_motion_scores_row", [ct.c_void_p, ct.c_int, U]+[ct.c_double]*3+[D], ct.c_int),
    "positions": ("ce_source_motion_positions", [ct.c_int, ct.c_int, F, F], ct.c_int),
}


def install(engine):
    global _FUNCTIONS
    functions = {key: bind_native(engine.dll, name, args, result)
                 for key, (name, args, result) in SIGNATURES.items()}
    if not all(functions.values()):
        _FUNCTIONS = None
        return False
    if functions["abi"]() != 1 or functions["size"]() != ct.sizeof(DirectionSpec):
        raise RuntimeError("native source motion ABI mismatch")
    _FUNCTIONS = functions
    from .native_source_motion_clock import install as install_clock
    return install_clock(engine)


def available():
    return _FUNCTIONS is not None


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_SOURCE_MOTION_") and name.endswith("_CNT")}


def _invoke(kind, args):
    if not available():
        _SOURCE_MOTION_FALLBACK_CNT[0] += 1
        raise RuntimeError("native source motion is not installed")
    _SOURCE_MOTION_CALL_CNT[0] += 1
    status = _FUNCTIONS[kind](*args)
    if kind != "fade" and status:
        _SOURCE_MOTION_ERROR_CNT[0] += 1
        raise RuntimeError(f"native source motion {kind} failed: {status}")
    _SOURCE_MOTION_SUCCESS_CNT[0] += 1
    return status


def direction(request):
    refs = []
    item = direction_spec(request, refs)
    gains, done, arrived = (np.empty(512, dtype) for dtype in
                            (np.float32, np.uint8, np.uint8))
    _invoke("direction", [ct.byref(item), borrowed_pointer(gains, F),
                          borrowed_pointer(done, U), borrowed_pointer(arrived, U)])
    _SOURCE_MOTION_DIRECTION_CNT[0] += 1
    return gains, done.view(np.bool_), arrived.view(np.bool_)


def transition_codes(engine, clip):
    enabled = engine._clip_transition_enabled(clip)
    codes = [CURVES.get(engine._clip_transition_curve(clip, fade_in=edge), 0)
             for edge in (True, False)]
    strategies = [STRATEGIES.get(engine._clip_transition_strategy(clip, fade_in=edge), 0)
                  for edge in (True, False)]
    return int(enabled), *codes, *strategies


def fade(engine, clip, local_t):
    args = [float(getattr(clip, "duration", 0.) or 0.),
            float(getattr(clip, "fade_in", 0.) or 0.),
            float(getattr(clip, "fade_out", 0.) or 0.), float(local_t)]
    result = _invoke("fade", args + list(transition_codes(engine, clip)))
    _SOURCE_MOTION_FADE_CNT[0] += 1
    return result


def _vector(value, dtype, kind, refs):
    if np.asarray(value).shape != (512,):
        raise ValueError("source vector must contain 512 channels")
    return pointer(value, dtype, kind, refs)


def force(request):
    refs = []
    table = source_table(request, refs)
    output, shaped = np.empty(512, np.uint8), ct.c_double()
    args = [table.times, table.frames, table.count] + [request[k] for k in
            ("source_t", "duration", "accel", "cruise", "decel")]
    args += [_vector(request["motor"], np.uint8, U, refs),
             _vector(request["raw"], np.uint8, U, refs), borrowed_pointer(output, U),
             ct.byref(shaped)]
    _invoke("force", args)
    _SOURCE_MOTION_FORCE_CNT[0] += 1
    return output, shaped.value


def scores(request):
    refs = []
    table = source_table(request, refs)
    output = np.empty(512, np.float64)
    args = [table.times, table.frames, table.count] + [request[k] for k in
            ("source_t", "start", "rate", "hold_cap")]
    args += [_vector(request["active"], np.uint8, U, refs), borrowed_pointer(output, D)]
    _invoke("scores", args)
    _SOURCE_MOTION_SCORE_CNT[0] += 1
    return output


def remap(request):
    refs = []
    values = [source_table(table, refs) for table in request["tables"]]
    tables = (SourceTable*len(values))(*values)
    values = [CopyRow(*row) for row in request["rows"]]
    rows = (CopyRow*len(values))(*values)
    item = remap_spec(request, refs)
    output = np.empty(512, np.uint8)
    raw = _vector(request["raw"], np.uint8, U, refs)
    _invoke("remap", [tables, len(tables), rows, len(rows), ct.byref(item),
                       raw, borrowed_pointer(output, U)])
    _SOURCE_MOTION_REMAP_CNT[0] += 1
    return output


def groups(positions, count, parallel):
    refs = []
    position = np.ascontiguousarray(positions, np.float32)
    output = [np.empty(position.size, np.float32) for _ in range(3)]
    _invoke("groups", [pointer(position, np.float32, F, refs), len(position), count,
                        int(parallel), *(borrowed_pointer(v, F) for v in output)])
    return tuple(output)


def scores_row(request):
    refs = []
    changes = request["changes"]
    ptr, size = None, 0
    if changes is not None:
        changes = np.ascontiguousarray(changes)
        if changes.dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
            changes = changes.astype(np.float64)
        if changes.shape != (512,):
            raise ValueError("score row must contain 512 channels")
        ptr, size = changes.ctypes.data, changes.dtype.itemsize
    output = np.empty(512, np.float64)
    args = [ptr, size, _vector(request["active"], np.uint8, U, refs), request["start"],
            request["rate"], request["hold_cap"], borrowed_pointer(output, D)]
    _invoke("scores_row", args)
    _SOURCE_MOTION_SCORE_CNT[0] += 1
    return output


def positions(direction, count):
    modes = {"rtl": 1, "btf": 1, "ctr": 2, "rtc": 3, "alt": 4,
             "alt_block": 5, "random": 6, "wave": 7, "cascade": 8,
             "rev_cascade": 9, "zigzag": 10}
    count = int(count)
    if not -(2**31) <= count <= 2**31-1:
        raise ValueError("direction size outside native int range")
    refs, order = [], None
    if direction == "random" and count > 1:
        order = np.arange(count, dtype=np.float32)
        np.random.default_rng(seed=count*42+7).shuffle(order)
    result = np.empty(count if count > 1 else 1, np.float32)
    _invoke("positions", [modes.get(direction, 0), count,
        pointer(order, np.float32, F, refs), borrowed_pointer(result, F)])
    _SOURCE_MOTION_POSITION_CNT[0] += 1
    return result
