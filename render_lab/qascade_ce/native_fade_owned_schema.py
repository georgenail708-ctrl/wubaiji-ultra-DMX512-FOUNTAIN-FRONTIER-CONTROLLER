"""Independent fixed source admission; existing C03 stamps remain untouched."""
from dataclasses import dataclass
import importlib
import sys
from types import FunctionType

from .native_fade_owned_signatures import OWN, SOURCE, NAMES, EXTRA


@dataclass(frozen=True)
class Source:
    core: tuple
    proxy: tuple
    own: tuple


@dataclass(frozen=True)
class Owner:
    source: object
    registry: object
    wrappers: tuple


def metadata(function):
    return function, function.__code__, function.__defaults__, function.__kwdefaults__


def same_function(function, row):
    return (type(function) is FunctionType and function is row[0] and function.__code__ is row[1]
            and function.__defaults__ is row[2] and function.__kwdefaults__ is row[3])


def wrapper_metadata(function):
    cells = tuple((name, cell, None if name == 'wrappers' else cell.cell_contents)
                  for name, cell in zip(function.__code__.co_freevars, function.__closure__ or ()))
    return metadata(function), function.__globals__, cells


def wrapper_current(function, row, wrappers):
    metadata_row, namespace, cells = row
    return (same_function(function, metadata_row) and function.__globals__ is namespace
            and all(cell.cell_contents is (wrappers if name == 'wrappers' else value) for name, cell, value in cells))


def modules():
    return tuple(importlib.import_module(__package__ + '.' + name) for name in NAMES)


def _locate(module, name):
    pieces = name.split('.')
    target = module if len(pieces) == 1 else vars(module).get(pieces[0])
    if target is not module and type(target) is not type:
        return None
    return vars(target).get(pieces[-1])


def fixed(module, name, function, expected):
    from .native_fade_target_guard import digest
    if type(function) is not FunctionType or function.__globals__ is not vars(module):
        return False
    if digest(function) != expected:
        return False
    defaults = (False,) if name.endswith('__init__') and '_K2FrameCacheNetworkProxy' in name else None
    if name.endswith('update_layer') or name.endswith('clear_layer'):
        defaults = (None,)
    return _defaults(function, defaults)


def _defaults(function, expected):
    actual = function.__defaults__
    valid = actual is None if expected is None else type(actual) is tuple and len(actual) == len(expected)
    return (valid and (expected is None or all(left is right for left, right in zip(actual, expected)))
            and function.__kwdefaults__ is None and _closure_original(function))


def _closure_original(function):
    if not function.__closure__:
        return True
    if function.__code__.co_freevars != ('__dataclass_builtins_object__',):
        return False
    return len(function.__closure__) == 1 and function.__closure__[0].cell_contents is object


def own_original():
    from . import native_fade_owned_install as install, native_fade_owned_scope as scope
    import dataclasses, functools, threading, weakref, _weakref, _thread
    aliases = ((install, 'commit', sys.modules[__package__ + '.native_programmer_transaction'].commit),
               (install, 'change', sys.modules[__package__ + '.native_programmer_transaction'].change),
               (install, 'wraps', functools.wraps), (scope, 'RLock', threading.RLock),
               (scope, 'ref', weakref.ref), (sys.modules[__name__], 'dataclass', dataclasses.dataclass))
    return (all(vars(module).get(name) is value for module, name, value in aliases)
            and weakref.ref is _weakref.ref and Source.__getattribute__ is object.__getattribute__
            and Owner.__getattribute__ is object.__getattribute__ and Source.__new__ is object.__new__
            and Owner.__new__ is object.__new__ and FunctionType is type(metadata)
            and threading._CRLock is _thread.RLock)


def own_rows():
    rows = []
    for module in modules():
        for name in NAMES[module.__name__.rsplit('.', 1)[-1]]:
            function = _locate(module, name)
            if not fixed(module, name, function, OWN[module.__name__ + ':' + name]):
                return None
            rows.append((module, name, metadata(function)))
    extra = _extra_rows()
    return None if extra is None else tuple(rows) + extra


def _extra_rows():
    from .native_fade_target_guard import digest
    rows = []
    for module_name, names in EXTRA.items():
        module = importlib.import_module(module_name)
        for name in names:
            function = _locate(module, name)
            if type(function) is not FunctionType or function.__globals__ is not vars(module):
                return None
            if digest(function) != SOURCE[module_name + ':' + name]:
                return None
            default = (vars(module)['write'],) if name == 'commit' else None
            if not _defaults(function, default):
                return None
            rows.append((module, name, metadata(function)))
    return tuple(rows)


def core_rows(core):
    from . import native_fade_target_guard as guard
    if type(core) is not guard.FadeGuard or guard.FadeGuard.__getattribute__ is not object.__getattribute__:
        return None
    methods = tuple(core.class_rows.items())
    if not _raw_current(core, methods):
        return None
    if not all(fixed(guard, 'FadeGuard.' + name, row[0], SOURCE['FadeGuard.' + name]) for name, row in methods):
        return None
    if not core.source_current() or not core.binding_current():
        return None
    functions = tuple(core.module_rows) + tuple(core.supplier_rows)
    values = tuple(core.globals) + tuple(core.supplier_values)
    from . import native_fade_target_install as install
    entry = next(row for row in core.module_rows if row[0] is vars(install) and row[1] == 'guard_for')
    return core, core.class_rows, methods, functions, values, type(core), core.module_rows, tuple(core.module_rows), entry


def _raw_current(core, methods):
    return (all(same_function(vars(type(core)).get(name), row) for name, row in methods)
            and all(same_function(namespace.get(name), row) for namespace, name, row in (*core.module_rows, *core.supplier_rows)))


def proxy_rows():
    package = sys.modules[__package__]
    kind = vars(package).get('_K2FrameCacheNetworkProxy')
    if type(kind) is not type or kind.__bases__ != (object,) or kind.__getattribute__ is not object.__getattribute__:
        return None
    names = ('__init__', '__getattr__', 'update_layer', 'clear_layer', 'flush_universes_now', 'notify_clip_takeover')
    rows = tuple((name, metadata(vars(kind)[name])) for name in names)
    if not all(fixed(package, '_K2FrameCacheNetworkProxy.' + name, row[0],
                     SOURCE['_K2FrameCacheNetworkProxy.' + name]) for name, row in rows):
        return None
    fields = tuple((name, vars(kind).get(name)) for name in ('_target', '_channel_defaults', '__hash__', '__eq__', '__getattribute__'))
    builtins = tuple((vars(package), name, vars(package).get(name)) for name in ('bool', 'getattr', 'str', 'int', 'min', 'len'))
    if any(value is not None for _namespace, _name, value in builtins):
        return None
    if vars(package).get('np') is not sys.modules.get('numpy'):
        return None
    builtins += ((vars(package), 'np', vars(package)['np']),)
    return kind, rows, fields, builtins, package


def create(core):
    old = core_rows(core)
    if old is None:
        return None
    own, proxy = own_rows(), proxy_rows()
    if own is None or old is None or proxy is None or not own_original():
        return None
    globals_rows = tuple((vars(module), name, vars(module).get(name)) for module, _name, row in own
                        for name in row[1].co_names if name not in ('_OWNER',))
    return Source(old, proxy, (own, globals_rows, tuple((module.__name__, module) for module in modules())))
