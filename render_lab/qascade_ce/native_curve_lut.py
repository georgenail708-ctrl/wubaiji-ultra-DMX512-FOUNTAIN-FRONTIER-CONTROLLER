"""One real CE cold LUT call; original public state/cache/error ownership.

Reference: original CurveMapping Bezier32 and fixed NumPy interpolation kernel.
No DLL reload, model monkeypatch, user setting or numerical Python preparation.
"""
import ctypes as ct
import functools
import logging

import numpy as np

from .native_binding import bind_native, borrowed_pointer
from . import native_curve_lut_model as model


log = logging.getLogger(__name__)
F64, F32, U8 = (ct.POINTER(kind) for kind in (ct.c_double, ct.c_float, ct.c_uint8))
_FUNCTION = None
_CURVE_LUT_CALL_CNT = [0]
_CURVE_LUT_SUCCESS_CNT = [0]
_CURVE_LUT_VALUES_CNT = [0]
_CURVE_LUT_POINTS_CNT = [0]
_CURVE_LUT_ERROR_CNT = [0]
_CURVE_LUT_FALLBACK_CNT = [0]
_CURVE_LUT_UNSUPPORTED_CNT = [0]


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_CURVE_LUT_") and name.endswith("_CNT")}


def available():
    return _FUNCTION is not None


def evaluate(points, flags):
    """All arrays stay strongly owned in this frame through the synchronous C."""
    if not available():
        raise RuntimeError("native CurveMapping LUT is not installed")
    result = np.empty(256, np.float32)
    _CURVE_LUT_CALL_CNT[0] += 1
    code = _FUNCTION(len(points), borrowed_pointer(points, F64),
                     borrowed_pointer(flags, U8), borrowed_pointer(result, F32))
    if code:
        raise RuntimeError(f"native CurveMapping LUT failed: {code}")
    _CURVE_LUT_SUCCESS_CNT[0] += 1
    _CURVE_LUT_VALUES_CNT[0] += 256
    _CURVE_LUT_POINTS_CNT[0] += len(points)
    return result


def _fallback(engine, curve, original):
    _CURVE_LUT_FALLBACK_CNT[0] += 1
    return original(engine, curve)


def _dispatch(engine, curve, original):
    if curve is None:
        return None
    try:
        if not model.supported(curve):
            _CURVE_LUT_UNSUPPORTED_CNT[0] += 1
            return original(engine, curve)
        key, points, flags = model.payload(curve)
        cached = model.lookup(engine, key)
        if cached is not None:
            return cached
        result = evaluate(points, flags)
        model.store(engine, key, result)
        return result
    except Exception:
        _CURVE_LUT_ERROR_CNT[0] += 1
        log.exception("native CurveMapping LUT failed")
        return _fallback(engine, curve, original)


def _wrapper(original):
    @functools.wraps(original)
    def wrapped(engine, curve):
        return _dispatch(engine, curve, original)
    return wrapped


def install(engine):
    """Bind the installed DLL and the one original public helper once."""
    from src.engine.playback_engine import PlaybackEngine
    global _FUNCTION
    _FUNCTION = bind_native(engine.dll, "ce_curve_lut_eval", [ct.c_int64, F64, U8, F32], ct.c_int)
    if not available():
        return False
    if not PlaybackEngine.__dict__.get("_ce_curve_lut_installed", False):
        original = PlaybackEngine._seq_delay_ease_lut
        PlaybackEngine._seq_delay_ease_lut = _wrapper(original)
        PlaybackEngine._ce_curve_lut_installed = True
    return True
