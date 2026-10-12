"""Cold numeric table generation with original preprocessing and cache ownership."""
import ctypes as ct
import hashlib
import inspect
import warnings
import _warnings
from types import BuiltinFunctionType, FunctionType

import numpy as np
from numpy import _core as numpy_core

from .native_curve_scalar_guard import _signature

EXPECTED = '55ee49abc9262718d0a0fa0ced487e4e835b9628bc2bf43a9c5b1f2e47ae166d'
EXPECTED_DISPATCHER = 'b6be62059e81a55b1e6d6060c9590b62ce8e5fd35365325550ac4b137fc220f8'
EXPECTED_NUMPY = 'b73dd97eed4f0fa915c0b8c0300f0b8cd9c05573810e3b8f006448f7ebf66a04'
EXPECTED_GETERR = 'f8ac4d97a00c7b48ddd91ac5d2a92ac1afcae4486f3e9aef529754140b87a0aa'
COUNTERS = dict(call=0, native=0, foreign=0, error=0)
_GUARD = None
_MISSING = object()
_DEFAULT_ERRORS = dict(divide='warn', over='warn', under='ignore', invalid='warn')
_DEPENDENCIES = ('zeros', 'arange', 'maximum', 'where', 'clip', 'float32',
                 'float64', 'int64', 'uint8', 'bool_', 'ndarray', 'empty', 'ascontiguousarray', 'geterr')
U, D, F = ct.POINTER(ct.c_uint8), ct.POINTER(ct.c_double), ct.POINTER(ct.c_float)
_ABI = (U, D, U, ct.c_int64, ct.c_int64, ct.c_int64, ct.c_int64, U, ct.c_int64, F, ct.c_int64)


def metadata(function):
    if type(function) is FunctionType:
        return function, function.__code__, function.__defaults__, function.__kwdefaults__
    return None


def source_digest(function):
    return hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()


class TableGuard:
    def __init__(self, owner_type, native_engine):
        from src.engine import playback_engine
        self.owner, self.engine = owner_type, native_engine
        self.namespace = vars(playback_engine)
        function = inspect.getattr_static(owner_type, '_compiled_clip_universe')
        self.original = metadata(function)
        self.dispatcher = metadata(self.namespace.get('_try_clip_change_times'))
        self.fallback = metadata(self.namespace.get('_numpy_clip_change_times'))
        self.function = getattr(native_engine, 'ce_cold_clip_change_times_v1', None)
        self.numpy_fields = {name: getattr(np, name) for name in _DEPENDENCIES}
        self.error_getter = metadata(np.geterr)
        self.error_binding = numpy_core._multiarray_umath._get_extobj_dict
        self.safe = self.canonical(function)

    def canonical(self, function):
        if type(function) is not FunctionType:
            return False
        return (function.__globals__ is self.namespace and source_digest(function) == EXPECTED
                and self.defaults_canonical(function)
                and all(value is getattr(numpy_core, name) for name, value in self.numpy_fields.items())
                and self.owner.__getattribute__ is object.__getattribute__
                and self.getter_canonical() and self.dispatcher_canonical() and self.fallback_canonical()
                and self.native_current() and self.dependencies_current())

    def dispatcher_canonical(self):
        function = self.namespace.get('_try_clip_change_times')
        return (self.dispatcher is not None and function.__globals__ is self.namespace
                and function.__defaults__ is None and function.__kwdefaults__ is None
                and source_digest(function) == EXPECTED_DISPATCHER)

    def dispatcher_current(self):
        current = metadata(self.namespace.get('_try_clip_change_times'))
        return current is not None and all(a is b for a, b in zip(current, self.dispatcher))

    def fallback_canonical(self):
        function = self.namespace.get('_numpy_clip_change_times')
        return (self.fallback is not None and function.__globals__ is self.namespace
                and function.__defaults__ is None and function.__kwdefaults__ is None
                and source_digest(function) == EXPECTED_NUMPY)

    def fallback_current(self):
        current = metadata(self.namespace.get('_numpy_clip_change_times'))
        return current is not None and all(a is b for a, b in zip(current, self.fallback))

    def defaults_canonical(self, function):
        defaults = function.__defaults__
        return (type(defaults) is tuple and len(defaults) == 3
                and all(value is None for value in defaults) and function.__kwdefaults__ is None)

    def getter_canonical(self):
        return (self.error_getter is not None and source_digest(np.geterr) == EXPECTED_GETERR
                and np.geterr.__defaults__ is None and np.geterr.__kwdefaults__ is None
                and type(self.error_binding) is BuiltinFunctionType
                and self.error_binding.__name__ == '_get_extobj_dict'
                and self.error_binding.__module__ == 'numpy._core._multiarray_umath'
                and self.getter_current())

    def getter_current(self):
        return (metadata(np.geterr) == self.error_getter
                and np.geterr.__globals__.get('_get_extobj_dict') is self.error_binding
                and numpy_core._multiarray_umath._get_extobj_dict is self.error_binding)

    def current(self, owner):
        if not self.safe or type(owner) is not self.owner or self.owner.__getattribute__ is not object.__getattribute__:
            return False
        if '_compiled_clip_universe' in vars(owner):
            return False
        function = inspect.getattr_static(self.owner, '_compiled_clip_universe')
        current = metadata(function)
        return (current is not None and all(a is b for a, b in zip(current, self.original))
                and self.dependencies_current() and self.native_current())

    def dependencies_current(self):
        return (self.namespace.get('np') is np
                and self.namespace.get('len', _MISSING) is _MISSING
                and all(getattr(np, name) is value for name, value in self.numpy_fields.items())
                and self.getter_current() and self.dispatcher_current() and self.fallback_current()
                and warnings.warn is _warnings.warn
                and np.geterr() == _DEFAULT_ERRORS)

    def native_current(self):
        return (isinstance(self.function, ct._CFuncPtr)
                and getattr(self.engine, 'ce_cold_clip_change_times_v1') is self.function
                and self.function.argtypes is not None and tuple(self.function.argtypes) == _ABI
                and self.function.restype is ct.c_int)


def _input_current(data, times, active):
    if any(type(value) is not np.ndarray for value in (data, times, active)):
        return False
    return (data.dtype == np.uint8 and data.shape == (times.size, 512)
            and times.dtype == np.float64 and times.ndim == 1
            and active.dtype == np.bool_ and active.shape == (512,))


def _invoke(data, times, active, function):
    data = np.ascontiguousarray(data)
    times = np.ascontiguousarray(times)
    active = np.ascontiguousarray(active)
    changed = np.empty(data.shape, np.uint8)
    out = np.empty(data.shape, np.float32)
    status = function(data.ctypes.data_as(U), times.ctypes.data_as(D), active.ctypes.data_as(U),
                      times.size, data.size, times.size, active.size,
                      changed.ctypes.data_as(U), changed.size, out.ctypes.data_as(F), out.size)
    if status < 0 or status > 3:
        raise RuntimeError(f'CE cold change-time table failed: {status}')
    return changed.view(np.bool_), out, status


def _warn_cast(status):
    if status & 1:
        warnings.warn('overflow encountered in cast', RuntimeWarning, stacklevel=3)
    if status & 2:
        warnings.warn('invalid value encountered in cast', RuntimeWarning, stacklevel=3)


def compute(owner, data, times, active):
    COUNTERS['call'] += 1
    guard = _GUARD
    if guard is None or not guard.current(owner) or not _input_current(data, times, active):
        COUNTERS['foreign'] += 1
        return None
    try:
        changed, out, status = _invoke(data, times, active, guard.function)
    except Exception:
        COUNTERS['error'] += 1
        return None
    COUNTERS['native'] += 1
    # Python warnings policy remains observable, including filters that raise.
    _warn_cast(status)
    return changed, out


def install(engine):
    global _GUARD
    from src.engine.playback_engine import PlaybackEngine
    _GUARD = TableGuard(PlaybackEngine, engine)
    if _GUARD.safe:
        _GUARD.namespace['_ce_clip_change_times'] = compute
    return _GUARD.safe
