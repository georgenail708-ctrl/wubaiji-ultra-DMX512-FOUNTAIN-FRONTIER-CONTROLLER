"""Canonical globals, classes, layouts and real DLL symbols; metadata only."""
import builtins
import ctypes as ct
import inspect
import sys
from types import MemberDescriptorType
from types import MethodType

from . import native_lt_prepare_metadata as metadata
from .native_lt_prepare_types import Binding, Packed, Request, Report, Value

CLIP_DEFAULTS = (False, 'pump', 128, .08, .22, 255, '#FFFFFF')
SLOTS = ('source', 'threshold', 'attack', 'release', 'color_is_white', 'cr', 'cg', 'cb', 'max_brightness')


def aliases(modules):
    from . import native_binding
    api, types, meta, guards, identity, scope = modules
    if api.MethodType is not MethodType:
        return False
    if type(types.CANONICAL) is not tuple or len(types.CANONICAL) != 5 or any(a is not b for a, b in
            zip(types.CANONICAL, (types.Value, types.Request, types.Report, types.Packed, types.Binding))):
        return False
    if api.bind_native is not native_binding.bind_native:
        return False
    if native_binding.CE_API_MAP is not sys.modules[__package__ + '.ce_api_map'].CE_API_MAP:
        return False
    if api.SYMBOLS != ('ce_lt_prepare_v1', 'ce_lt_prepare_abi', 'ce_lt_prepare_request_size',
                       'ce_lt_prepare_value_size', 'ce_lt_prepare_report_size'):
        return False
    for module in (api, types, meta, identity):
        if vars(module).get('ct') is not ct:
            return False
    if any(vars(module).get('metadata') is not meta for module in (api, identity, scope)):
        return False
    for name in ('Binding', 'Request', 'Report', 'Value', 'report'):
        if vars(api).get(name) is not vars(types)[name]:
            return False
    if meta.Packed is not types.Packed or meta.Value is not types.Value:
        return False
    return all(not any(name in vars(module) for name in vars(builtins) if not name.startswith('_'))
               for module in (*modules, native_binding))


def models(clip_type, params_type):
    from src.core import models as model_module
    from . import lt_path
    if clip_type is not model_module.Clip or params_type is not lt_path.LtParams:
        return False
    if CLIP_DEFAULTS != (False, 'pump', 128, .08, .22, 255, '#FFFFFF') or SLOTS != (
            'source', 'threshold', 'attack', 'release', 'color_is_white', 'cr', 'cg', 'cb', 'max_brightness'):
        return False
    if any(kind.__getattribute__ is not object.__getattribute__ or
           kind.__setattr__ is not object.__setattr__ or kind.__new__ is not object.__new__
           for kind in (clip_type, params_type)):
        return False
    for name, expected in zip(metadata.FIELDS, CLIP_DEFAULTS):
        actual = inspect.getattr_static(clip_type, name, metadata.MISSING)
        if type(actual) is not type(expected) or actual != expected:
            return False
    if type(params_type.__slots__) is not tuple or params_type.__slots__ != SLOTS:
        return False
    return all(type(vars(params_type).get(name)) is MemberDescriptorType
               and vars(params_type)[name].__objclass__ is params_type for name in SLOTS)


def layouts():
    from . import native_lt_prepare_types as types
    for kind, fields, options, bases in types.LAYOUTS:
        if kind.__bases__ is not bases:
            return False
        if any(vars(kind).get(name) is not descriptor for name, descriptor in fields):
            return False
        if any(vars(kind).get(name, types.MISSING) is not value for name, value in options):
            return False
    expected = ((Value, [('kind', ct.c_uint32), ('selector', ct.c_uint32), ('offset', ct.c_uint64),
                         ('length', ct.c_uint64), ('number', ct.c_double)], 32),
        (Request, [('abi', ct.c_uint32), ('struct_size', ct.c_uint32), ('mode', ct.c_uint32),
                   ('count', ct.c_uint32), ('integers', ct.POINTER(ct.c_uint8)),
                   ('integer_size', ct.c_uint64), ('values', Value * 4)], 160),
        (Report, [(name, ct.c_uint32) for name in ('abi', 'struct_size', 'semantic', 'selector')]
                 + [(name, ct.c_uint64) for name in ('consumed', 'threshold_values', 'attack_values',
                                                    'release_values', 'brightness_values')]
                 + [('threshold', ct.c_int32), ('brightness', ct.c_int32),
                    ('attack', ct.c_double), ('release', ct.c_double)], 80))
    for kind, fields, size in expected:
        if ct.sizeof(kind) != size or type(kind._fields_) is not list or len(kind._fields_) != len(fields):
            return False
        if any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or row[0] != name or row[1] is not scalar
               for row, (name, scalar) in zip(kind._fields_, fields)):
            return False
    return (type(metadata.PLAIN) is tuple and len(metadata.PLAIN) == 6
            and all(left is right for left, right in zip(metadata.PLAIN, (bool, int, float, str, bytes, type(None))))
            and type(metadata.FIELDS) is tuple and all(type(name) is str for name in metadata.FIELDS) and metadata.FIELDS == (
        'light_tracking_enabled', 'light_tracking_source', 'light_tracking_threshold',
        'light_tracking_attack', 'light_tracking_release', 'light_tracking_max_brightness', 'light_tracking_color'))


class NativeIdentity:
    def __init__(self, binding):
        from .ce_api_map import CE_API_MAP
        from .native_lt_prepare import SYMBOLS
        self.binding, self.engine, self.dll = binding, binding.engine, binding.engine.dll
        self.functions = binding.functions
        self.symbols = tuple(CE_API_MAP.get(name, name) for name in SYMBOLS)
        self.map = CE_API_MAP
        self.lt_function = self.engine.ce_lt_apply
        self.lt_abi = (tuple(self.lt_function.argtypes), self.lt_function.restype)
        self.lt_symbol = CE_API_MAP.get('ce_lt_apply', 'ce_lt_apply')
        self.errchecks = tuple(getattr(function, 'errcheck', metadata.MISSING) for function in self.functions)
        self.lt_errcheck = getattr(self.lt_function, 'errcheck', metadata.MISSING)
        self.abi = tuple((tuple(function.argtypes), function.restype) for function in self.functions)

    def current(self, owner):
        from . import native_lt_prepare as api
        native = sys.modules.get(__package__)
        if api._OWNER is not owner or owner.binding is not self.binding or self.engine.dll is not self.dll:
            return False
        if self.engine is not getattr(native, '_engine', None) or self.binding.engine is not self.engine:
            return False
        if self.binding.functions is not self.functions:
            return False
        from .ce_api_map import CE_API_MAP
        from .native_lt_prepare import SYMBOLS
        if CE_API_MAP is not self.map or tuple(CE_API_MAP.get(name, name) for name in SYMBOLS) != self.symbols:
            return False
        if self.engine.ce_lt_apply is not self.lt_function:
            return False
        if CE_API_MAP.get('ce_lt_apply', 'ce_lt_apply') != self.lt_symbol or vars(self.dll).get(self.lt_symbol) is not self.lt_function:
            return False
        actual = self.lt_function.argtypes
        if not isinstance(self.lt_function, ct._CFuncPtr) or type(actual) not in (tuple, list) or len(actual) != len(self.lt_abi[0]):
            return False
        if any(a is not b for a, b in zip(actual, self.lt_abi[0])) or self.lt_function.restype is not self.lt_abi[1]:
            return False
        if getattr(self.lt_function, 'errcheck', metadata.MISSING) is not self.lt_errcheck:
            return False
        for function, symbol, (arguments, result), errcheck in zip(self.functions, self.symbols, self.abi, self.errchecks):
            if not isinstance(function, ct._CFuncPtr) or vars(self.dll).get(symbol) is not function:
                return False
            actual = function.argtypes
            if type(actual) not in (list, tuple) or len(actual) != len(arguments):
                return False
            if any(left is not right for left, right in zip(actual, arguments)) or function.restype is not result:
                return False
            if getattr(function, 'errcheck', metadata.MISSING) is not errcheck:
                return False
        return True
