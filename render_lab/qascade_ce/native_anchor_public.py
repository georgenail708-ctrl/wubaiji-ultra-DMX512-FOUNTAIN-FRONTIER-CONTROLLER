"""Qualified T5 reduction after original buffers/index, before original math.

The original public method keeps its warm return, allocation/index order and
shared cache publication. None continues its untouched original math tail;
this helper never retries preprocessing, publishes a cache or evicts a row.
"""
import ctypes
import hashlib
import inspect
import sys
from types import FunctionType

import numpy as np
from numpy import _core as numpy_core
from numpy._core import numeric as numpy_numeric

from .native_curve_scalar_guard import _signature

EXPECTED = {'_get_clip_anchor': '6f5f914542de9ab36f9db16e10fc367f3577cd9ddcab8547f74c84df00e21371', '_clip_frame_index_for_universe': '71a384b44321e598d0d36b8ed8b3a261c7d7cf0acfc515838fba5f008e28c778', '_evict_oldest': '4d1ace21e71fa5652ba99d701d2a39ef9022e9b235e8f2ad6a00d129770db9bc', 'reduce': '67c4c74227301037f006bc96d90038d118c7a1ef194d32aa1badfeed180c4e5a'}  # Explicit frozen reviewed code metadata, filled once by builder.
COUNTERS = dict(call=0, native=0, cache=0, foreign=0, error=0)
_GUARD = None
_MISSING = object()
_U8 = ctypes.POINTER(ctypes.c_uint8)
_F32 = ctypes.POINTER(ctypes.c_float)


def _metadata(function):
    if isinstance(function, staticmethod):
        function = function.__func__
    return function, function.__code__, function.__defaults__, function.__kwdefaults__


def _canonical(function, name, namespace):
    return (type(function) is FunctionType and function.__globals__ is namespace
        and function.__defaults__ is None and function.__kwdefaults__ is None
        and hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() == EXPECTED[name])


class AnchorGuard:
    def __init__(self, engine_type, native_engine):
        from src.core import models
        from src.engine import playback_engine
        self.owner, self.clip, self.frame = engine_type, models.Clip, models.DMXFrame
        self.models, self.native_engine = models, native_engine
        self.function = getattr(native_engine, "ce_clip_anchor", None)
        self.namespace = vars(playback_engine)
        names = ("_get_clip_anchor", "_clip_frame_index_for_universe", "_evict_oldest")
        self.originals = {name: _metadata(inspect.getattr_static(engine_type, name)) for name in names}
        self.safe = all(_canonical(row[0], name, self.namespace) for name, row in self.originals.items())
        self.numpy = self.namespace.get("np")
        self.numpy_fields = {name: getattr(np, name) for name in
            ("asarray", "full", "zeros", "empty", "ascontiguousarray", "minimum", "maximum", "float32", "int16", "uint8", "bool_", "ndarray")}
        self.global_fields = {name: self.namespace.get(name, _MISSING) for name in
            ("min", "len", "int", "bool", "getattr", "id", "float")}
        self.accessors = {cls: cls.__getattribute__ for cls in (engine_type, self.clip, self.frame)}
        self.fields = {cls: {name: inspect.getattr_static(cls, name, _MISSING) for name in names}
            for cls, names in ((engine_type, ("_clip_anchor_cache", "_anchor_cache_cap")),
                (self.clip, ("id", "frames")), (self.frame, ("data", "universe", "timestamp")))}
        self.reducer = _metadata(reduce)
        self.safe = (self.safe and self.numpy is np and np is sys.modules["numpy"]
            and isinstance(self.function, ctypes._CFuncPtr)
            and all(value is getattr(numpy_core, name) for name, value in self.numpy_fields.items())
            and self.numpy_fields["full"] is numpy_numeric.full
            and all(value is _MISSING for value in self.global_fields.values())
            and all(value is object.__getattribute__ for value in self.accessors.values())
            and all(value is _MISSING for row in self.fields.values() for value in row.values())
            and _canonical(reduce, "reduce", vars(sys.modules[__name__]))
            and self.namespace.get("_ce_clip_anchor_reduce") in (None, reduce))

    def current(self, owner):
        if not self.safe or type(owner) is not self.owner:
            return False
        if self.models.Clip is not self.clip or self.models.DMXFrame is not self.frame:
            return False
        for cls, function in self.accessors.items():
            if cls.__getattribute__ is not function:
                return False
            if any(inspect.getattr_static(cls, name, _MISSING) is not value
                   for name, value in self.fields[cls].items()):
                return False
        if self.namespace.get("np") is not self.numpy:
            return False
        if any(getattr(np, name) is not value for name, value in self.numpy_fields.items()):
            return False
        if any(self.namespace.get(name, _MISSING) is not value for name, value in self.global_fields.items()):
            return False
        if (self.namespace.get("_ce_clip_anchor_reduce") is not reduce
                or any(a is not b for a, b in zip(_metadata(reduce), self.reducer))):
            return False
        for name, row in self.originals.items():
            if name in vars(owner):
                return False
            actual = inspect.getattr_static(type(owner), name)
            if isinstance(actual, staticmethod):
                actual = actual.__func__
            if actual is not row[0] or any(a is not b for a, b in zip(_metadata(row[0]), row)):
                return False
        return True

    def inputs(self, clip, active):
        return (type(clip) is self.clip and type(active) is np.ndarray
            and active.dtype == np.bool_ and active.shape == (512,)
            and type(vars(clip).get("frames")) is list and type(vars(clip).get("id")) is str)

    def frames(self, frames):
        if type(frames) is not list or len(frames) > 2147483647:
            return False
        for frame in frames:
            if type(frame) is not self.frame:
                return False
            data = vars(frame).get("data")
            if type(data) is not np.ndarray or data.dtype != np.uint8:
                return False
        return True


def reduce(owner, clip, universe, active, frames):
    """Return exact new T5 output or None; original math/cache remains the caller."""
    guard = _GUARD
    COUNTERS["call"] += 1
    if (guard is None or not guard.current(owner) or not guard.inputs(clip, active)
            or type(universe) not in (int, bool) or not guard.frames(frames)
            or getattr(guard.native_engine, "ce_clip_anchor", None) is not guard.function):
        COUNTERS["foreign"] += 1
        return None
    try:
        rows = np.zeros((len(frames), 512), np.uint8)
        for index, frame in enumerate(frames):
            source = frame.data.reshape(-1)
            size = min(source.size, 512)
            rows[index, :size] = source[:size]
        current_mask = np.ascontiguousarray(active, np.uint8)
        anchor = np.empty(512, np.float32)
        guard.function(rows.ctypes.data_as(_U8), len(frames),
            current_mask.ctypes.data_as(_U8), anchor.ctypes.data_as(_F32))
    except Exception:
        COUNTERS["error"] += 1
        return None
    COUNTERS["native"] += 1
    return anchor


def install(engine):
    global _GUARD
    if _GUARD is not None:
        return True
    from src.engine.playback_engine import PlaybackEngine
    guard = AnchorGuard(PlaybackEngine, engine)
    if not guard.safe:
        return False
    _GUARD = guard
    guard.namespace["_ce_clip_anchor_reduce"] = reduce
    return True
