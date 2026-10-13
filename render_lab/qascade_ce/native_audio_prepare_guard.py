"""Deduplicated exact-source metadata; checked once for each public batch."""
import builtins
import importlib
import inspect
import sys
from types import FunctionType, BuiltinFunctionType

from .native_filter_prepare_guard import code_digest, function_metadata, function_current
from .native_filter_prepare_scope import locate
from .native_audio_prepare_signatures import EXPECTED, DEFAULTS, NEW_NAMES, SOURCE_NAMES, SOURCE_DATA
from .native_audio_prepare_source_signatures import ADMITTED_SOURCE_EXPECTED
from . import native_audio_prepare_types as types

MISSING = object()
RUNTIME = frozenset(('_OWNER', '_UNAVAILABLE', 'SCOPE_TOKEN', 'PROCESSORS_FUNCTION', 'STATE_FUNCTION', 'POINTS_FUNCTION', 'ROUND_FUNCTION'))


def modules():
    own = [sys.modules[__package__ + '.' + name] for name in NEW_NAMES]
    sources = [importlib.import_module(name) for name in SOURCE_NAMES]
    return own, sources


def snapshot(value):
    import struct
    if type(value) is float:
        return 'float', struct.pack('=d', value)
    if type(value) is dict:
        return 'dict', tuple((snapshot(key), snapshot(item)) for key, item in value.items())
    if type(value) in (list, tuple):
        return type(value).__name__, tuple(snapshot(item) for item in value)
    if type(value) in (str, bytes, int, bool, type(None)):
        return type(value).__name__, value
    return MISSING


def _fixed(module, name, function):
    key = module.__name__ + ':' + name
    if type(function) is not FunctionType or function.__globals__ is not vars(module):
        return False
    expected = ADMITTED_SOURCE_EXPECTED if key in ADMITTED_SOURCE_EXPECTED else EXPECTED
    if code_digest(function) != expected.get(key):
        return False
    return _defaults_original(module, function, DEFAULTS[key])


def _defaults_original(module, function, expected):
    defaults, keyword = expected
    actual = function.__defaults__
    if defaults is None:
        valid = actual is None
    else:
        valid = type(actual) is tuple and len(actual) == len(defaults) and all(
            _default_entry(module, value, wanted) for value, wanted in zip(actual, defaults))
    if not valid:
        return False
    actual_keyword = function.__kwdefaults__
    if keyword is None:
        return actual_keyword is None
    return (type(actual_keyword) is dict and len(actual_keyword) == len(keyword)
            and all(_default_entry(module, actual_keyword.get(name, MISSING), value) for name, value in keyword))


def _default_entry(module, value, expected):
    kind, literal = expected
    return value is vars(module).get(literal, MISSING) if kind == 'reference' else snapshot(value) == literal


def _function_rows(modules, originals):
    rows = []
    for module in modules:
        names = NEW_NAMES.get(module.__name__.split('.')[-1], SOURCE_NAMES.get(module.__name__, ()))
        for name in names:
            target, local, function = locate(module, name)
            function = originals.get((module.__name__, name), function)
            if not _fixed(module, name, function):
                return None
            rows.append((module, target, local, function))
    return rows


def _globals(rows, overrides):
    captured = {}
    for module, _target, _name, function in rows:
        for name in function.__code__.co_names:
            if name not in RUNTIME:
                key = module.__name__, name
                value = overrides.get(key, vars(module).get(name, MISSING))
                captured[key] = (vars(module), name, value)
    return tuple(captured.values())


def _functions(rows):
    captured = {}
    for _module, _target, _name, function in rows:
        cells = tuple((cell, cell.cell_contents) for cell in function.__closure__ or ())
        captured[id(function)] = function_metadata(function), cells
    return tuple(captured.values())


def _source_data(sources):
    rows = []
    for module in sources:
        for name in SOURCE_DATA.get(module.__name__, ()):
            value = vars(module).get(name, MISSING)
            if snapshot(value) != SOURCE_DATA[module.__name__][name]:
                return None
            rows.append((vars(module), name, value, snapshot(value)))
    return tuple(rows)


def _no_shadow(modules):
    names = set(vars(builtins))
    return all(not any(name in vars(module) for name in names if not name.startswith('_')) for module in modules)


def _stdlib_original(sources):
    import math, copy
    from . import native_audio_prepare_dependencies as dependencies
    catalog = next(module for module in sources if module.__name__ == 'src.engine.audio_mod_processor_schema')
    state = next(module for module in sources if module.__name__ == 'src.engine.audio_modulation')
    return (dependencies.standard_original() and catalog.math is math and state.math is math and state.copy is copy
            and type(math.isfinite) is BuiltinFunctionType and math.isfinite.__module__ == 'math'
            and math.isfinite.__name__ == 'isfinite')


class Guard:
    def __init__(self, originals, wrappers, aliases):
        own, sources = modules()
        overrides = {(module.__name__, name): value for module, name, value in aliases}
        rows = _function_rows((*own, *sources), originals)
        self.safe = rows is not None and _no_shadow((*own, *sources)) and _stdlib_original(sources)
        self.safe = self.safe and _aliases_initial(aliases) and _source_aliases_initial()
        self.modules = tuple((module.__name__, module) for module in (*own, *sources))
        self.sources = {module.__name__: module for module in sources}
        self.data = _source_data((*own, *sources))
        self.safe = self.safe and self.data is not None
        self.functions = () if rows is None else _functions(rows)
        self.globals = () if rows is None else _globals(rows, overrides)
        self.bindings = tuple(aliases)
        self.function_bindings = () if rows is None else tuple((vars(target), name,
            overrides.get((module.__name__, name), function)) for module, target, name, function in rows)
        self.originals, self.wrappers = originals, wrappers
        self.original_items, self.wrapper_items = tuple(originals.items()), tuple(wrappers.items())
        self.wrapper_rows = tuple((function_metadata(wrapper), tuple((cell, cell.cell_contents) for cell in wrapper.__closure__ or ()))
                                  for wrapper in wrappers.values())
        self._suppliers()
        self.copy_dispatch = _copy_dispatch()
        self.kinds = _kind_rows()
        self.safe = self.safe and _dispatch_initial(self.copy_dispatch) and _kinds_initial(self.kinds)
        self.owner, self.binding = None, None

    def _suppliers(self):
        from . import native_audio_prepare_dependencies as dependencies
        self.safe = self.safe and dependencies.preflight()

    def attach(self, owner):
        from .ce_api_map import CE_API_MAP
        from .native_audio_prepare import _specifications
        self.owner, self.binding = owner, owner.binding
        self.engine = owner.binding.engine
        self.dll = vars(self.engine).get('dll', MISSING)
        self.native = tuple((name, function, tuple(function.argtypes), function.restype,
                             CE_API_MAP.get(_specifications()[name][0], _specifications()[name][0]))
                            for name, function in owner.binding.functions.items())
        self.engine_access = type(self.engine).__getattribute__
        self.functions_map = owner.binding.functions
        self.map = CE_API_MAP
        self.map_rows = tuple((_specifications()[name][0], symbol) for name, _fn, _args, _restype, symbol in self.native)

    def sources_current(self):
        if not self.safe:
            return False
        if not all(namespace.get(name, MISSING) is value for namespace, name, value in self.globals):
            return False
        if not all(getattr(module, name, MISSING) is value for module, name, value in self.bindings):
            return False
        if not all(namespace.get(name, MISSING) is function for namespace, name, function in self.function_bindings):
            return False
        return all((all(namespace.get(name, MISSING) is value and snapshot(value) == frozen
                        for namespace, name, value, frozen in self.data), _dispatch_current(self.copy_dispatch),
                    _kind_current(self.kinds), _mapping_current(self.originals, self.original_items),
                    _mapping_current(self.wrappers, self.wrapper_items)))

    def binding_current(self):
        from . import native_audio_prepare as api
        from . import native_audio_prepare_dependencies as dependencies, ce_api_map
        if not dependencies.engine_original(self.engine) or ce_api_map.CE_API_MAP is not self.map:
            return False
        return self._binding_identity() and self._native_current(api) and dependencies.map_original()

    def _binding_identity(self):
        return all((vars(self.owner).get('binding') is self.binding, self.binding.engine is self.engine,
                    type(self.engine).__getattribute__ is self.engine_access,
                    vars(self.engine).get('dll', MISSING) is self.dll, self.binding.functions is self.functions_map))

    def _native_current(self, api):
        pointers = all(self.functions_map[name] is function and vars(self.dll).get(symbol) is function
                       for name, function, _args, _restype, symbol in self.native)
        aliases = all(vars(api).get(name + '_FUNCTION') is self.functions_map[name]
                      for name in ('PROCESSORS', 'STATE', 'POINTS', 'ROUND'))
        signatures = all(_signature_current(function, arguments, restype)
                         for _name, function, arguments, restype, _symbol in self.native)
        return pointers and aliases and signatures


def _signature_current(function, arguments, restype):
    import ctypes as ct
    current = function.argtypes
    return (isinstance(function, ct._CFuncPtr) and type(current) in (list, tuple) and len(current) == len(arguments)
            and all(left is right for left, right in zip(current, arguments)) and function.restype is restype)


def _aliases_initial(aliases):
    return all(vars(module).get(name) is wrapper or vars(module).get(name) is vars(wrapper).get('__wrapped__')
               for module, name, wrapper in aliases)


def _source_aliases_initial():
    from src.engine import audio_mod_processor_schema as schema, audio_modulation as state
    from src.engine import audio_mod_processor_state as processor_state, audio_mod_envelope as envelope
    from src.engine import audio_mod_spread as spread, audio_mod_folder_scope as folder
    return (state._processor_fields is processor_state.processor_fields and processor_state.processor_key is schema.processor_key
            and processor_state.normalize_processors is schema.normalize_processors
            and state.normalize_points is envelope.normalize_points and state.clamp_deg is spread.clamp_deg
            and folder.normalize_state is state.normalize_state)


def _copy_dispatch():
    import copy
    dispatch = copy._deepcopy_dispatch
    return copy, dispatch, tuple(dispatch.items())


def _dispatch_current(captured):
    module, dispatch, entries = captured
    current = module._deepcopy_dispatch
    if current is not dispatch or type(current) is not dict or len(current) != len(entries):
        return False
    return all(current.get(key, MISSING) is value for key, value in entries)


def _dispatch_initial(captured):
    import types, weakref
    module, dispatch, _entries = captured
    required = ((list, module._deepcopy_list), (tuple, module._deepcopy_tuple), (dict, module._deepcopy_dict),
                (types.MethodType, module._deepcopy_method))
    atomic = (type(None), type(Ellipsis), type(NotImplemented), int, float, bool, complex, bytes, str,
              type(_dispatch_initial.__code__), type, range, type(len), type(_dispatch_initial), weakref.ref, property)
    return (type(dispatch) is dict and len(dispatch) == len(required) + len(atomic)
            and all(dispatch.get(kind) is function for kind, function in required)
            and all(dispatch.get(kind) is module._deepcopy_atomic for kind in atomic))


def _kind_rows():
    from . import native_audio_prepare_owner as owner
    classes = (types.Binding, types.Packed, types.Result, owner.Owner, types.Step, types.Request, types.Report)
    return tuple((kind, kind.__getattribute__, tuple((name, vars(kind).get(name, MISSING)) for name in
                   ('__init__', '__new__', '__getattribute__', '_fields_'))) for kind in classes)


def _kinds_initial(rows):
    import ctypes as ct
    structures = (types.Step, types.Request, types.Report)
    getters = all(getter is (ct.Structure.__getattribute__ if kind in structures else object.__getattribute__)
                  for kind, getter, _fields in rows)
    plain = all(type(kind) is type and kind.__bases__ == (object,) and kind.__new__ is object.__new__
                for kind, _getter, _fields in rows if kind not in structures)
    return getters and plain


def _kind_current(rows):
    return all(kind.__getattribute__ is getter and all(vars(kind).get(name, MISSING) is value for name, value in fields)
               for kind, getter, fields in rows)


def _mapping_current(mapping, entries):
    return type(mapping) is dict and len(mapping) == len(entries) and all(mapping.get(key, MISSING) is value for key, value in entries)
