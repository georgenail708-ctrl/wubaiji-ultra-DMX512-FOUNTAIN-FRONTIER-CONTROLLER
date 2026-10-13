"""C03 candidate numeric-only facade. Product integration is ROOT-owned.

No public method is patched by this module. Caller qualifies original metadata
and performs original preprocessing once; these functions accept plain owning
or borrowed ndarrays only. They do not publish caches, callbacks or state.
"""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer

U8 = ct.POINTER(ct.c_uint8)
F32 = ct.POINTER(ct.c_float)
I64 = ct.POINTER(ct.c_int64)
INT32_MAX = 2**31 - 1
NATIVE = None
COUNTERS = dict(signature_calls=0, signature_data_values=0,
    signature_active_values=0, target_calls=0, target_values=0,
    motor_values=0, cache=0, foreign=0, errors=0, fallbacks=0)
SIGNATURES = {
    'signature': ('ce_fade_target_signature',
        [U8, ct.c_int32, U8, ct.c_int32, I64, I64]),
    'target': ('ce_fade_target_frame',
        [F32, ct.c_int32, U8, ct.c_int32, U8, F32, I64]),
}


def counters():
    return COUNTERS.copy()


def _plain(value, dtype):
    return (type(value) is np.ndarray and value.dtype == dtype
            and value.size <= INT32_MAX)


def _array(value):
    # Dtype was already converted exactly once by the original public caller.
    return np.ascontiguousarray(value).reshape(-1)


def _call(kind, arguments):
    if NATIVE is None:
        return False
    function = NATIVE[kind]
    try:
        if function(*arguments) != 0:
            COUNTERS['errors'] += 1
            return False
    except Exception:
        COUNTERS['errors'] += 1
        return False
    COUNTERS[kind + '_calls'] += 1
    return True


def signature(data, active):
    """Exact original four scalars, or None for original reducer fallback."""
    if not _plain(data, np.uint8) or not _plain(active, np.bool_):
        COUNTERS['foreign'] += 1
        return None
    source, mask = _array(data), _array(active)
    result, report = (ct.c_int64 * 4)(), (ct.c_int64 * 4)()
    if not _call('signature', (borrowed_pointer(source, U8), source.size,
            borrowed_pointer(mask, U8), mask.size, result, report)):
        COUNTERS['fallbacks'] += 1
        return None
    COUNTERS['signature_data_values'] += report[0]
    COUNTERS['signature_active_values'] += report[1]
    return tuple(result)


def target(data, active, motor):
    """New exact 512/f32 idle frame, or None for original numeric fallback."""
    if (not _plain(data, np.float32) or not _plain(active, np.bool_)
            or not _plain(motor, np.bool_) or motor.shape != (512,)):
        COUNTERS['foreign'] += 1
        return None
    source, mask, motors = _array(data), _array(active), _array(motor)
    output, report = np.empty(512, np.float32), (ct.c_int64 * 4)()
    if not _call('target', (borrowed_pointer(source, F32), source.size,
            borrowed_pointer(mask, U8), mask.size, borrowed_pointer(motors, U8),
            borrowed_pointer(output, F32), report)):
        COUNTERS['fallbacks'] += 1
        return None
    COUNTERS['target_values'] += report[2]
    COUNTERS['motor_values'] += report[3]
    return output


def install(engine):
    """Bind only the already-loaded original DLL; never reload or create hooks."""
    global NATIVE
    dll = getattr(engine, 'dll', engine)
    functions = {key: bind_native(dll, name, args, ct.c_int)
        for key, (name, args) in SIGNATURES.items()}
    if any(not isinstance(function, ct._CFuncPtr) for function in functions.values()):
        return False
    NATIVE = functions
    return True
