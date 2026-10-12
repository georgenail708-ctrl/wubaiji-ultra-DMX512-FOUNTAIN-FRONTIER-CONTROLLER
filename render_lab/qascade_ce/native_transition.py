"""Sequential anchor handoff; reuse the installed DLL and original state."""
import ctypes as ct

from .native_binding import bind_native

NATIVE = None
COUNTERS = dict(anchor_calls=0, anchor_values=0, blend_calls=0, blend_values=0,
                errors=0, fallbacks=0)


def install(engine):
    global NATIVE
    NATIVE = bind_native(engine.dll, "ce_prepared_anchor_update",
        [ct.c_void_p, ct.c_void_p, ct.c_void_p, ct.c_double, ct.c_void_p, ct.c_void_p], ct.c_int)
    return NATIVE is not None


def counters():
    return COUNTERS.copy()
