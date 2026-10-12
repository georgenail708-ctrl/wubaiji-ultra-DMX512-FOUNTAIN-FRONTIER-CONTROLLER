"""Owned AudioMod bindings; reuse the installed CE DLL and opaque map once."""
import ctypes as ct
from contextvars import ContextVar

import numpy as np

from .native_binding import bind_native, borrowed_pointer

_BINDING = None
_REPORT = ContextVar("native_audio_report", default=None)
_COUNTERS = {key: 0 for key in (
    "math_call", "math_success", "math_work", "chain_call", "chain_success",
    "chain_work", "scope_call", "scope_success", "scope_work", "fallback",
    "unsupported", "override", "error")}


def install(engine):
    """Bind domain functions without modifying CEEngine's legacy registry."""
    global _BINDING
    types = [ct.POINTER(ct.c_int64), ct.POINTER(ct.c_double),
             ct.POINTER(ct.c_void_p)]
    functions = {kind: bind_native(engine.dll, f"ce_audio_{kind}", types, ct.c_int)
                 for kind in ("math", "chain", "scope")}
    _BINDING = functions if all(functions.values()) else None
    return available()


def available():
    return _BINDING is not None


def counters():
    return dict(_COUNTERS)


def report():
    return _REPORT.get()


def invoke(kind, integers, scalars, arrays):
    """Only native success advances work counters; arrays stay strongly alive."""
    if not available():
        return None
    _COUNTERS[f"{kind}_call"] += 1
    integers = np.ascontiguousarray(integers, dtype=np.int64)
    scalars = np.ascontiguousarray(scalars, dtype=np.float64)
    pointers = (ct.c_void_p * len(arrays))(*[a.ctypes.data for a in arrays])
    result = _BINDING[kind](borrowed_pointer(integers, ct.POINTER(ct.c_int64)),
                            borrowed_pointer(scalars, ct.POINTER(ct.c_double)), pointers)
    if result:
        _COUNTERS["error"] += 1
        raise RuntimeError(f"native audio {kind} rejected input: {result}")
    _COUNTERS[f"{kind}_success"] += 1
    return arrays[-1]


def process(data, active, rows, plan, features):
    if not available():
        return None
    from .native_audio_plan import process_chain
    result, details = process_chain(data, active, (rows, plan, features))
    _REPORT.set(details)
    _COUNTERS["chain_work"] += int(details[0])
    return result
