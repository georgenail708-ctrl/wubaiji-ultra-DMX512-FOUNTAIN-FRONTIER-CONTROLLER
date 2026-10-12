"""One bound geometry domain; public metadata supplies owned numeric inputs."""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer
from . import native_source_motion as motion

D, F, I, U = (ct.POINTER(kind) for kind in
               (ct.c_double, ct.c_float, ct.c_int, ct.c_uint8))
_FUNCTIONS = None
_SOURCE_GEOMETRY_CALL_CNT = [0]
_SOURCE_GEOMETRY_SUCCESS_CNT = [0]
_SOURCE_GEOMETRY_ERROR_CNT = [0]
_SOURCE_GEOMETRY_FALLBACK_CNT = [0]


class GeometrySpec(ct.Structure):
    _fields_ = [(name, D) for name in ("xy", "scope", "shapes", "reference")] + [
        (name, ct.c_int) for name in ("n", "sn", "ns", "mode", "flags",
                                     "clockwise", "start_valid")] + [("start", ct.c_double)]


SIGNATURES = {
    "abi": ("ce_source_geometry_abi", [], ct.c_int),
    "size": ("ce_source_geometry_size", [], ct.c_int),
    "primitive": ("ce_source_geometry_primitive", [ct.c_int, ct.c_int, D, D], ct.c_int),
    "metrics": ("ce_source_geometry_metrics", [ct.c_int, D, D, D], ct.c_int),
    "fit": ("ce_source_geometry_fit", [ct.POINTER(GeometrySpec), D], ct.c_int),
    "rank": ("ce_source_geometry_rank", [ct.POINTER(GeometrySpec), F, I], ct.c_int),
    "slots": ("ce_source_geometry_slots", [ct.POINTER(GeometrySpec), I], ct.c_int),
    "assign": ("ce_source_geometry_assign", [ct.POINTER(GeometrySpec), U, I], ct.c_int),
    "infer": ("ce_source_geometry_infer", [ct.POINTER(GeometrySpec), I, D, I], ct.c_int),
    "groups": ("ce_source_geometry_groups", [ct.c_int, ct.c_int, I, I], ct.c_int),
    "linear": ("ce_fade_dir_positions", [D, D, ct.c_int, D, D, ct.c_int, ct.c_int, F], ct.c_int),
    "ordered": ("ce_ordered_fixtures", [F, ct.c_int, I], None),
}


def install(engine):
    global _FUNCTIONS
    functions = {key: bind_native(engine.dll, name, args, result)
                 for key, (name, args, result) in SIGNATURES.items()}
    if not all(functions.values()):
        _FUNCTIONS = None
        return False
    if functions["abi"]() != 1 or functions["size"]() != ct.sizeof(GeometrySpec):
        raise RuntimeError("native source geometry ABI mismatch")
    _FUNCTIONS = functions
    if not hasattr(motion, "_SOURCE_MOTION_GEOMETRY_CNT"):
        motion._SOURCE_MOTION_GEOMETRY_CNT = [0]
    return True


def available():
    return _FUNCTIONS is not None


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_SOURCE_GEOMETRY_") and name.endswith("_CNT")}


def _invoke(kind, args, validate=None):
    if not available():
        _SOURCE_GEOMETRY_FALLBACK_CNT[0] += 1
        raise RuntimeError("native source geometry is not installed")
    _SOURCE_GEOMETRY_CALL_CNT[0] += 1
    result = _FUNCTIONS[kind](*args)
    expected = 1 if kind == "linear" else 0
    if kind != "ordered" and result != expected:
        _SOURCE_GEOMETRY_ERROR_CNT[0] += 1
        raise RuntimeError(f"native source geometry {kind} failed: {result}")
    if validate is not None:
        try:
            validate()
        except Exception:
            _SOURCE_GEOMETRY_ERROR_CNT[0] += 1
            raise
    _SOURCE_GEOMETRY_SUCCESS_CNT[0] += 1
    motion._SOURCE_MOTION_GEOMETRY_CNT[0] += 1


def _count(value):
    if not 0 <= int(value) <= 2**31-1:
        raise ValueError("geometry count outside native int range")
    return int(value)


def _array(value, columns):
    array = np.ascontiguousarray(value, dtype=np.float64).reshape(-1, columns)
    _count(len(array))
    return array


def _spec(request, refs):
    item = GeometrySpec()
    for name, count_name, width in (("xy", "n", 2), ("scope", "sn", 2), ("shapes", "ns", 5)):
        value = _array(request.get(name, []), width)
        refs.append(value)
        setattr(item, name, borrowed_pointer(value, D))
        setattr(item, count_name, len(value))
    reference = request.get("reference")
    if reference is not None:
        value = _array(reference, 5)
        if len(value) != 1:
            raise ValueError("geometry reference must contain one shape")
        refs.append(value)
        item.reference = borrowed_pointer(value, D)
    for name in ("mode", "flags", "clockwise", "start_valid", "start"):
        setattr(item, name, request.get(name, 0))
    return item


def primitive(op, values, count=None):
    values = np.ascontiguousarray(values, np.float64)
    size = values.size if count is None else count
    output = np.empty(2, np.float64)
    _invoke("primitive", [op, _count(size), borrowed_pointer(values, D), borrowed_pointer(output, D)])
    return output


def metrics(shape, coords):
    coords, shape = _array(coords, 2), _array(shape, 5)
    if len(shape) != 1:
        raise ValueError("one shape required for metrics")
    result = np.empty((len(coords), 2), np.float64)
    _invoke("metrics", [len(coords), borrowed_pointer(coords, D), borrowed_pointer(shape, D),
                        borrowed_pointer(result, D)])
    return result


def fit(request):
    refs, result = [], np.empty(8, np.float64)
    item = _spec(request, refs)
    _invoke("fit", [ct.byref(item), borrowed_pointer(result, D)])
    return result


def ranks(request):
    refs, available_flag = [], ct.c_int()
    item = _spec(request, refs)
    result = np.empty(item.n, np.float32)
    _invoke("rank", [ct.byref(item), borrowed_pointer(result, F), ct.byref(available_flag)])
    return result if available_flag.value else None


def slots(request):
    refs = []
    item = _spec(request, refs)
    result = np.full(item.n, -1, np.int32)
    _invoke("slots", [ct.byref(item), borrowed_pointer(result, I)], lambda: _check_order(result))
    return result


def linear(direction, coords, scope):
    modes = {"ltr": 0, "rtl": 1, "ftb": 2, "btf": 3, "ctr": 4, "rtc": 5}
    xy, sxy = _array(coords, 2), _array(scope, 2)
    columns = [np.ascontiguousarray(value[:, axis]) for value in (xy, sxy) for axis in (0, 1)]
    output = np.empty(len(xy), np.float32)
    args = [borrowed_pointer(value, D) for value in columns]
    _invoke("linear", args[:2]+[len(xy)]+args[2:]+[len(sxy), modes[direction],
                       borrowed_pointer(output, F)])
    return output


def _check_order(result):
    if sorted(result.tolist()) != list(range(len(result))):
        raise RuntimeError("native geometry returned an incomplete ordering")


def ordered(positions):
    positions = np.ascontiguousarray(positions, np.float32).reshape(-1)
    count = _count(len(positions))
    result = np.full(count, -1, np.int32)
    _invoke("ordered", [borrowed_pointer(positions, F), count, borrowed_pointer(result, I)],
            lambda: _check_order(result))
    return result


def assignments(request, used):
    refs = []
    item = _spec(request, refs)
    used = np.ascontiguousarray(used, np.uint8)
    if used.shape != (item.n,):
        raise ValueError("geometry assignment mask has wrong shape")
    result = np.empty(item.n, np.int32)
    _invoke("assign", [ct.byref(item), borrowed_pointer(used, U), borrowed_pointer(result, I)])
    return result


def inferred(coords):
    refs, count = [], ct.c_int()
    item = _spec(dict(xy=coords), refs)
    bounds, centers = np.empty((item.n, 2), np.int32), np.empty((item.n, 2), np.float64)
    _invoke("infer", [ct.byref(item), borrowed_pointer(bounds, I), borrowed_pointer(centers, D),
                      ct.byref(count)])
    if not 0 <= count.value <= item.n:
        raise RuntimeError("native geometry returned invalid segment count")
    return bounds[:count.value], centers[:count.value]


def groups(mode, count, values):
    count = _count(count)
    values = np.ascontiguousarray(values, np.int32)
    shape = (count, 3) if mode == 1 else ((count, 2) if mode == 0 else count)
    output = np.empty(shape, np.int32)
    _invoke("groups", [mode, count, borrowed_pointer(values, I), borrowed_pointer(output, I)])
    return output
