"""Native output safety math with the original Python state ownership."""
import ctypes as ct

import numpy as np
from numpy._core.umath import clip as _clip_cast_policy

from .native_binding import bind_native, borrowed_pointer

U = ct.POINTER(ct.c_uint8)
F = ct.POINTER(ct.c_float)
D = ct.POINTER(ct.c_double)
I = ct.POINTER(ct.c_int)
_FUNCTIONS = None
_FILTER_CALL_CNT = [0]
_FILTER_SUCCESS_CNT = [0]
_FILTER_ROW_CNT = [0]
_FILTER_SUPPRESS_CNT = [0]
_FILTER_ERROR_CNT = [0]
_FILTER_FALLBACK_CNT = [0]
_FILTER_CLAMP_CNT = [0]
PLAIN_KINDS = {("b", 1): 0, ("u", 1): 0, ("i", 1): 1,
    ("i", 2): 2, ("u", 2): 3, ("i", 4): 4, ("u", 4): 5,
    ("i", 8): 6, ("u", 8): 7, ("f", 4): 8, ("f", 8): 9,
    ("f", 2): 10, ("c", 8): 11, ("c", 16): 12}


class FilterSpec(ct.Structure):
    _fields_ = [(n, U) for n in ("pump", "coarse", "fine", "light")] + [
        ("home", F), ("last", F), ("spans", D),
    ] + [(n, ct.c_double) for n in
         ("pump_scale", "motor_scale", "dps", "local_t", "prev_t")] + [
        (n, ct.c_int) for n in ("prev_valid", "finalize", "suppress")]


def install(engine):
    global _FUNCTIONS
    args = [ct.c_int, ct.POINTER(FilterSpec), F, F, D, I, I]
    functions = {
        "eval": bind_native(engine.dll, "ce_output_filter", args, ct.c_int),
        "suppress": bind_native(engine.dll, "ce_output_filter_suppress",
                                [ct.c_void_p, U, ct.c_int, ct.c_int], ct.c_int),
        "abi": bind_native(engine.dll, "ce_output_filter_abi", [], ct.c_int),
        "size": bind_native(engine.dll, "ce_output_filter_size", [], ct.c_int),
        "clamp": bind_native(engine.dll, "ce_output_filter_clamp",
                              [ct.c_void_p, ct.c_int, ct.c_int, U], ct.c_int),
    }
    if not all(functions.values()):
        _FUNCTIONS = None
        return False
    if functions["abi"]() != 1 or functions["size"]() != ct.sizeof(FilterSpec):
        raise RuntimeError("native output filter ABI mismatch")
    _FUNCTIONS = functions
    return True


def available():
    return _FUNCTIONS is not None


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_FILTER_") and name.endswith("_CNT")}


def _pointer(value, dtype, kind, refs):
    array = np.asarray(value)
    if dtype is np.uint8 and array.dtype == np.bool_:
        array = array.view(np.uint8)
    array = np.ascontiguousarray(array, dtype=dtype)
    refs.append(array)
    return borrowed_pointer(array, kind)


def _spec(request, refs):
    from src.engine import clip_output_filters as public
    engine, clip, universe, proj = (request[k] for k in ("engine", "clip", "universe", "proj"))
    masks = public.channel_type_masks(engine, universe, proj)
    motor, dps, pump = public.clip_filter_params(clip)
    item = FilterSpec(pump_scale=pump, motor_scale=motor, dps=dps,
                      local_t=request["local_t"], finalize=request["finalize"],
                      suppress=public.light_suppressed(proj))
    for dest, source in (("pump", "pump"), ("coarse", "motor_coarse"),
                         ("fine", "motor_fine"), ("light", "light")):
        setattr(item, dest, _pointer(masks[source], np.uint8, U, refs))
    item.home = _pointer(masks["motor_home"], np.float32, F, refs)
    spans = public.universe_deg_spans(engine, universe, proj)
    item.spans = _pointer([spans[k] for k in ("motor_x", "motor_y", "motor_z")],
                         np.float64, D, refs)
    key = (str(getattr(clip, "id", "") or id(clip)), int(universe), round(float(dps), 4))
    store = getattr(engine, public._STATE_ATTR, None)
    prev = store.get(key) if isinstance(store, dict) and len(store) <= 64 else None
    if prev is not None:
        item.last = _pointer(prev[0], np.float32, F, refs)
        item.prev_t, item.prev_valid = float(prev[1]), 1
    return item, key


def apply(request):
    """Return native output and commit rate state only after actual success."""
    from src.engine import clip_output_filters as public
    if not available():
        _FILTER_FALLBACK_CNT[0] += 1
        return None
    data = request["data"]
    if data is None:
        return data
    if request["finalize"] and not public.filters_active(request["clip"]):
        return _plain_finalize(request)
    refs = []
    item, key = _spec(request, refs)
    arr = np.asarray(data)
    if arr.shape != (512,):
        raise ValueError("clip filter requires 512 channels")
    native = np.ascontiguousarray(arr, dtype=np.float32)
    rate = np.empty(512, np.float32)
    rate_t, writes, clears = ct.c_double(), ct.c_int(), ct.c_int()
    _FILTER_CALL_CNT[0] += 1
    status = _FUNCTIONS["eval"](1, ct.byref(item), borrowed_pointer(native, F),
        borrowed_pointer(rate, F), ct.byref(rate_t), ct.byref(writes), ct.byref(clears))
    if status:
        _FILTER_ERROR_CNT[0] += 1
        raise RuntimeError(f"native output filter failed: {status}")
    _commit_rate(request, key, (rate, rate_t.value), (writes.value, clears.value))
    native = _restore_dtype(request, arr, native)
    _FILTER_SUCCESS_CNT[0] += 1
    _FILTER_ROW_CNT[0] += 1
    return native


def _plain_finalize(request):
    from src.engine import clip_output_filters as public
    arr = np.asarray(request["data"])
    _validate_plain_cast(request, arr)
    kind = PLAIN_KINDS.get((arr.dtype.kind, arr.dtype.itemsize))
    if kind is None or arr.size > 2**31-1:
        raise ValueError("unsupported plain filter numeric dtype or size")
    native = np.ascontiguousarray(arr, dtype=arr.dtype.newbyteorder("="))
    refs, mask = [], U()
    if public.light_suppressed(request["proj"]):
        selected = public.light_channel_mask(request["engine"], request["universe"], request["proj"])
        if selected.size == arr.size:
            if arr.ndim != 1:
                raise IndexError("light mask requires a one-dimensional output")
            mask = _pointer(selected, np.uint8, U, refs)
    _FILTER_CALL_CNT[0] += 1
    status = _FUNCTIONS["clamp"](native.ctypes.data, native.size, kind, mask)
    if status:
        _FILTER_ERROR_CNT[0] += 1
        raise RuntimeError(f"native plain filter clamp failed: {status}")
    arr[...] = native.reshape(arr.shape)
    _FILTER_SUCCESS_CNT[0] += 1
    _FILTER_CLAMP_CNT[0] += 1
    return arr


def _validate_plain_cast(request, arr):
    """Reuse NumPy's original cast policy without running channel arithmetic."""
    try:
        _clip_cast_policy.resolve_dtypes((arr.dtype, float, float, arr.dtype), casting="same_kind")
    except TypeError:
        from src.engine import clip_output_filters as public
        public.apply_light_suppress(request["engine"], request["universe"], arr, request["proj"])
        raise


def _commit_rate(request, key, history, actions):
    from src.engine import clip_output_filters as public
    if actions[0]:
        store = public._rate_store(request["engine"])
        store[key] = history
    if actions[1]:
        public.clear_rate_state(request["engine"], request["clip"])


def _restore_dtype(request, arr, native):
    from src.engine import clip_output_filters as public
    if arr.dtype == np.float32 or not public.filters_active(request["clip"]):
        arr[...] = native
        return arr
    return native


def suppress(data, mask):
    if not available():
        _FILTER_FALLBACK_CNT[0] += 1
        return None
    arr = np.asarray(data)
    if arr.dtype.hasobject:
        raise TypeError("light output must use a numeric dtype")
    work = np.ascontiguousarray(arr)
    selected = np.ascontiguousarray(mask, dtype=np.uint8)
    if selected.size != work.size:
        raise ValueError("light mask size differs from output")
    status = _FUNCTIONS["suppress"](work.ctypes.data, borrowed_pointer(selected, U),
                                   work.size, -work.dtype.itemsize)
    if status:
        _FILTER_ERROR_CNT[0] += 1
        raise RuntimeError(f"native light suppression failed: {status}")
    arr[...] = work
    _FILTER_SUPPRESS_CNT[0] += 1
    return arr
