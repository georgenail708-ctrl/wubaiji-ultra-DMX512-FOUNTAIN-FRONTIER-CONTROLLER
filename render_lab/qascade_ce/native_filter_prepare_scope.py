"""Fixed supplier admission before native binding and retained binding identity."""
import builtins
import ctypes as ct
import hashlib
import struct
import sys
from types import CodeType, FunctionType

from .native_filter_prepare_signatures import SUPPLIER_EXPECTED
from .native_filter_prepare_successor_signatures import (
    ADMITTED_EXPECTED as NEW_EXPECTED, ADMITTED_FUNCTION_NAMES as NEW_FUNCTION_NAMES)

MODULES = ('native_filter_prepare', 'native_filter_prepare_types', 'native_filter_prepare_metadata',
           'native_filter_prepare_guard', 'native_filter_prepare_owner', 'native_filter_prepare_scope')
OWN_DEFAULTS = {'native_filter_prepare_scope:defaults_original': ((None, None), None)}


def _constant(value):
    if isinstance(value, CodeType):
        return _shape(value)
    if type(value) is float:
        return ('float', struct.pack('=d', value).hex())
    if type(value) is tuple:
        return ('tuple', tuple(_constant(item) for item in value))
    return type(value).__name__, value


def _shape(code):
    return (code.co_code.hex(), tuple(_constant(value) for value in code.co_consts[1:]),
            code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_exceptiontable.hex())


def digest(function):
    return hashlib.sha256(repr(_shape(function.__code__)).encode()).hexdigest()


def canonical(module, name, table):
    _target, _name, function = locate(module, name)
    return (type(function) is FunctionType and function.__globals__ is vars(module)
            and digest(function) == table.get(module.__name__.split('.')[-1] + ':' + name))


def locate(module, name):
    parts = name.split('.')
    target = module if len(parts) == 1 else vars(module).get(parts[0])
    function = None if target is None else vars(target).get(parts[-1])
    return target, parts[-1], function


def default_value(value):
    if type(value) is float:
        return ('float', struct.pack('=d', value).hex())
    if type(value) is tuple:
        return ('tuple', tuple(default_value(item) for item in value))
    if type(value) in (str, bytes, bool, int, type(None)):
        return type(value).__name__, value
    return None


def defaults_original(function, expected=None, keyword=None):
    if type(function) is not FunctionType:
        return False
    if default_value(function.__defaults__) != default_value(expected):
        return False
    actual = function.__kwdefaults__
    if keyword is None:
        return actual is None
    return (type(actual) is dict and all(type(name) is str for name in actual) and actual.keys() == keyword.keys()
            and all(default_value(actual[name]) == default_value(value) for name, value in keyword.items()))


def own_defaults_original(function, qualified):
    expected, keyword = OWN_DEFAULTS.get(qualified, (None, None))
    return defaults_original(function, expected, keyword)


def suppliers_original():
    from . import native_binding as binding, native_curve_scalar_guard as curve
    from . import native_programmer_transaction as transaction
    suppliers = ((binding, ('bind_native',)), (curve, ('_normalize', '_signature')),
                 (transaction, ('read', 'write', 'change', 'validate', 'restore', 'commit')))
    if not all(canonical(module, name, SUPPLIER_EXPECTED) for module, names in suppliers for name in names):
        return False
    if not all(defaults_original(vars(module)[name]) for module, names in suppliers for name in names if name != 'commit'):
        return False
    return (type(transaction.commit.__defaults__) is tuple and len(transaction.commit.__defaults__) == 1
            and transaction.commit.__defaults__[0] is transaction.write
            and transaction.commit.__kwdefaults__ is None)


def supplier_rows():
    from . import native_binding as binding, native_curve_scalar_guard as curve
    from . import native_programmer_transaction as transaction
    return ((binding, ('bind_native',)), (curve, ('_normalize', '_signature')),
            (transaction, ('read', 'write', 'change', 'validate', 'restore', 'commit')))


def aliases_original(modules):
    import dataclasses, functools, inspect, threading, warnings, weakref
    import collections, contextvars, _warnings
    from . import native_binding, native_curve_scalar_guard, native_programmer_transaction
    api, types, metadata, guard, owner, scope = modules
    rows = ((api, 'bind_native', native_binding.bind_native), (api, 'Binding', types.Binding),
            (api, 'Report', types.Report), (api, 'array', types.array), (api, 'report', types.report),
            (metadata, 'Numbers', types.Numbers), (metadata, 'array', types.array),
            (guard, '_signature', native_curve_scalar_guard._signature), (owner, 'Guard', guard.Guard),
            (owner, 'change', native_programmer_transaction.change), (owner, 'commit', native_programmer_transaction.commit),
            (owner, 'wraps', functools.wraps), (owner, 'dataclass', dataclasses.dataclass),
            (owner, 'OrderedDict', collections.OrderedDict), (owner, 'ContextVar', contextvars.ContextVar),
            (owner, 'RLock', threading.RLock), (owner, 'local', threading.local),
            (types, 'dataclass', dataclasses.dataclass), (owner, 'warnings', warnings),
            (owner, '_warnings', _warnings), (owner, 'weakref', weakref), (metadata, 'inspect', inspect))
    if not all(vars(module).get(name) is value for module, name, value in rows):
        return False
    return (typed_original(types, metadata)
            and all(vars(module).get('ct') is ct for module in (api, types, metadata, scope))
            and all(vars(api).get(name) is vars(types)[name] for name in ('D', 'I', 'U'))
            and all(vars(module).get('metadata') is metadata for module in (api, owner, guard))
            and owner.api is api and guard.np is sys.modules.get('numpy'))


def typed_original(types, metadata):
    expected = [(name, ct.c_int) for name in ('abi', 'struct_size', 'semantic', 'semantic_selector', 'warning_axis')]
    expected += [(name, ct.c_uint64) for name in ('parameters', 'span_inputs', 'span_values', 'median_values', 'default_axes')]
    fields = types.Report._fields_
    if type(fields) is not list or len(fields) != len(expected):
        return False
    if not all(type(row) is tuple and len(row) == 2 and type(row[0]) is str
               and row[0] == name and row[1] is kind for row, (name, kind) in zip(fields, expected)):
        return False
    plain = (bool, int, float, str, bytes, type(None))
    if type(metadata.PLAIN) is not tuple or len(metadata.PLAIN) != len(plain):
        return False
    names = ('motor_limit_scale', 'motor_speed_limit_dps', 'pump_master_scale')
    return (types.D is ct.POINTER(ct.c_double) and types.I is ct.POINTER(ct.c_int) and types.U is ct.POINTER(ct.c_uint8)
            and all(value is expected for value, expected in zip(metadata.PLAIN, plain))
            and type(metadata.FIELDS) is tuple and all(type(value) is str for value in metadata.FIELDS)
            and metadata.FIELDS == names)


def preflight():
    modules = tuple(sys.modules[__package__ + '.' + name] for name in MODULES)
    if not functions_original(modules) or not suppliers_original() or not aliases_original(modules):
        return False
    for module in modules:
        if any(name in vars(module) for name in vars(builtins) if not name.startswith('_')):
            return False
    guard = modules[3]
    return guard._new_functions(modules) is not None


def functions_original(modules):
    for module in modules:
        for name in NEW_FUNCTION_NAMES[module.__name__.split('.')[-1]]:
            if not canonical(module, name, NEW_EXPECTED):
                return False
            if not own_defaults_original(locate(module, name)[2], module.__name__.split('.')[-1] + ':' + name):
                return False
    return True


def owner_current(owner):
    from . import native_filter_prepare_owner as module
    retained = module.SCOPE_RESOLVER
    if type(retained) is not tuple or len(retained) != 6 or retained[4] is not owner:
        return False
    guard = owner.guard
    for target, name, captured in guard.new_functions:
        function = vars(target).get(name)
        if type(function) is not FunctionType or function is not captured[0]:
            return False
        if function.__code__ is not captured[1] or function.__defaults__ is not captured[2] or function.__kwdefaults__ is not captured[3]:
            return False
    return guard.current()


class BindingScope:
    def __init__(self, owner):
        from .ce_api_map import CE_API_MAP
        self.owner, self.binding = owner, owner.binding
        self.engine, self.dll = owner.binding.engine, owner.binding.engine.dll
        self.functions = (owner.binding.parameters, owner.binding.spans, owner.binding.abi, owner.binding.size)
        names = ('ce_filter_prepare_params_v1', 'ce_filter_prepare_spans_v1',
                 'ce_filter_prepare_abi', 'ce_filter_prepare_size')
        self.symbols = tuple((CE_API_MAP.get(name, name), function) for name, function in zip(names, self.functions))
        self.signatures = tuple((tuple(function.argtypes), function.restype) for function in self.functions)

    def current(self, owner):
        from . import native_filter_prepare as api, native_filter_prepare_owner as module
        if owner is not self.owner or module._OWNER is not owner or owner.binding is not self.binding:
            return False
        if self.binding.engine is not self.engine or self.engine.dll is not self.dll:
            return False
        functions = (self.binding.parameters, self.binding.spans, self.binding.abi, self.binding.size)
        if not all(left is right for left, right in zip(functions, self.functions)):
            return False
        if api.PARAMETERS_FUNCTION is not functions[0] or api.SPANS_FUNCTION is not functions[1]:
            return False
        return all(self._function_current(function, symbol, signature)
                   for function, symbol, signature in zip(functions, self.symbols, self.signatures))

    def _function_current(self, function, symbol, signature):
        if not isinstance(function, ct._CFuncPtr) or vars(self.dll).get(symbol[0]) is not function:
            return False
        arguments = function.argtypes
        return (type(arguments) in (list, tuple) and len(arguments) == len(signature[0])
                and all(left is right for left, right in zip(arguments, signature[0]))
                and function.restype is signature[1])
