"""Fixed cold supplier/source checks independent of their mutable hash helpers."""
import ctypes as ct
import hashlib
import importlib
import struct
import sys
from types import CodeType, FunctionType

from .native_audio_prepare_signatures import EXPECTED, MAP_EXPECTED

SUPPLIERS = {
    'qascade_ce.native_filter_prepare_guard': ('code_digest', 'function_metadata', 'function_current', '_keyword_current'),
    'qascade_ce.native_filter_prepare_scope': ('locate',),
    'qascade_ce.native_filter_prepare_metadata': ('number',),
    'qascade_ce.native_binding': ('bind_native',),
    'qascade_ce.native_curve_scalar_guard': ('_signature', '_normalize'),
    'qascade_ce.native_programmer_transaction': ('read', 'write', 'change', 'validate', 'restore', 'commit', 'Change.__init__'),
}


def _constant(value):
    if isinstance(value, CodeType):
        return _signature(value)
    if type(value) is float:
        return 'float', struct.pack('=d', value).hex()
    if type(value) is tuple:
        return 'tuple', tuple(_constant(item) for item in value)
    return type(value).__name__, value


def _signature(code):
    return (code.co_code.hex(), tuple(_constant(value) for value in code.co_consts[1:]),
            code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_exceptiontable.hex())


def _builtin(function, name, module):
    return type(function) is type(len) and function.__name__ == name and function.__module__ == module


def standard_original():
    import _struct, _hashlib, types, math, copy, copyreg, weakref, marshal
    from . import native_audio_prepare_guard as guard, native_filter_prepare_guard as filter_guard
    from . import native_curve_scalar_guard as curve, native_audio_prepare_scope as scope
    if not all((ctypes_original(), struct.pack is _struct.pack, _builtin(struct.pack, 'pack', '_struct'),
                hashlib.sha256 is _hashlib.openssl_sha256, _builtin(hashlib.sha256, 'openssl_sha256', '_hashlib'),
                _builtin(math.isfinite, 'isfinite', 'math'), _builtin(marshal.dumps, 'dumps', 'marshal'))):
        return False
    if not all((FunctionType is type(_signature), CodeType is type(_signature.__code__),
                types.FunctionType is FunctionType, types.CodeType is CodeType, types.BuiltinFunctionType is type(len),
                guard.FunctionType is FunctionType, scope.FunctionType is FunctionType,
                filter_guard.FunctionType is FunctionType, curve.FunctionType is FunctionType,
                curve.CodeType is CodeType, guard.BuiltinFunctionType is type(len))):
        return False
    return (curve.struct is struct and curve.hashlib is hashlib and filter_guard.hashlib is hashlib
            and 'types' not in vars(copy) and 'weakref' not in vars(copy)
            and copy.dispatch_table is copyreg.dispatch_table
            and not any(kind in copy.dispatch_table for kind in (bool, int, float, str, bytes, bytearray, memoryview,
                                                               type(None), dict, list, tuple, set, frozenset)))


def ctypes_original():
    import _ctypes
    names = ('POINTER', 'sizeof', 'byref')
    return (ct.Structure is _ctypes.Structure and ct._CFuncPtr is _ctypes.CFuncPtr
            and all(vars(ct).get(name) is vars(_ctypes).get(name)
                    and _builtin(vars(ct).get(name), name, '_ctypes') for name in names))


def supplier_functions():
    rows = []
    for name, keys in SUPPLIERS.items():
        module = importlib.import_module(name)
        for key in keys:
            target = module if '.' not in key else vars(module)[key.split('.')[0]]
            function = vars(target).get(key.split('.')[-1])
            if type(function) is not FunctionType or function.__globals__ is not vars(module):
                return None
            digest = hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()
            if digest != EXPECTED.get(name + ':' + key):
                return None
            rows.append((module, target, key, function))
    return rows


def supplier_aliases():
    from . import native_audio_prepare_guard as guard, native_filter_prepare_guard as filter_guard
    from . import native_filter_prepare_scope as scope, native_binding as binding, native_curve_scalar_guard as curve
    from . import ce_api_map, native_programmer_transaction as transaction, native_audio_prepare_owner as owner
    import contextlib, dataclasses, functools, contextvars, threading
    rows = ((guard, 'code_digest', filter_guard.code_digest), (guard, 'function_metadata', filter_guard.function_metadata),
            (guard, 'function_current', filter_guard.function_current), (guard, 'locate', scope.locate),
            (filter_guard, '_signature', curve._signature), (binding, 'CE_API_MAP', ce_api_map.CE_API_MAP),
            (transaction, 'ExitStack', contextlib.ExitStack), (transaction, 'dataclass', dataclasses.dataclass),
            (owner, 'RollbackFailure', transaction.RollbackFailure), (owner, 'wraps', functools.wraps),
            (owner, 'dataclass', dataclasses.dataclass), (owner, 'ContextVar', contextvars.ContextVar),
            (owner, 'RLock', threading.RLock))
    return all(vars(module).get(name) is value for module, name, value in rows)


def preflight():
    if not standard_original() or not supplier_aliases() or not map_original():
        return False
    rows = supplier_functions()
    if rows is None:
        return False
    from . import native_audio_prepare_guard as guard
    return all(guard._defaults_original(module, function, guard.DEFAULTS[module.__name__ + ':' + name])
               for module, target, name, function in rows)


def map_original():
    from .ce_api_map import CE_API_MAP
    if type(CE_API_MAP) is not dict:
        return False
    return all(type(CE_API_MAP.get(name)) is str and CE_API_MAP[name] == symbol
               for name, symbol in MAP_EXPECTED.items())


def engine_original(engine):
    from . import ctypes_api
    package = sys.modules[__package__]
    kind = vars(ctypes_api).get('CEEngine')
    if type(kind) is not type or type(engine) is not kind or vars(package).get('_engine') is not engine:
        return False
    if kind.__getattribute__ is not object.__getattribute__ or any('__getattr__' in vars(base) for base in kind.__mro__):
        return False
    dll = vars(engine).get('dll')
    return (type(dll) is ct.CDLL and ct.CDLL.__getattribute__ is object.__getattribute__
            and vars(ctypes_api).get('ctypes') is ct)
