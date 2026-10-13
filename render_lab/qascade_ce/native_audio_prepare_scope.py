"""Pre-binding fixed source, one retained current admission per public batch."""
import builtins
import ctypes as ct
import importlib
import sys
from types import FunctionType

from . import native_audio_prepare_guard as guard
from . import native_audio_prepare_types as types
from . import native_audio_prepare_dependencies as dependencies

PLAIN = (bool, int, float, str, bytes, bytearray, memoryview, type(None))
CONTAINERS = (list, tuple, dict, set, frozenset)


def ordinary(value):
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if type(item) in PLAIN:
            continue
        if type(item) not in CONTAINERS:
            return False
        if id(item) in seen:
            continue
        seen.add(id(item))
        pending.extend(item.keys() if type(item) is dict else item)
        if type(item) is dict:
            pending.extend(item.values())
    return True


def _ctypes_original():
    classes = (types.Step, types.Request, types.Report)
    if any(type(kind) is not type(ct.Structure) or kind.__bases__ != (ct.Structure,) for kind in classes):
        return False
    if not all(kind.__init__ is ct.Structure.__init__ and kind.__new__ is ct.Structure.__new__
               and kind.__getattribute__ is ct.Structure.__getattribute__ for kind in classes):
        return False
    return types.D is ct.POINTER(ct.c_double) and types.I is ct.POINTER(ct.c_int) and _fields_original(classes)


def _fields_original(classes):
    expected = {
        'Step': [('op', ct.c_int), ('input', ct.c_int * 3), ('skip_to', ct.c_int), ('first', ct.c_int),
                 ('last', ct.c_int), ('auxiliary', ct.c_int), ('fallback', ct.c_double), ('low', ct.c_double), ('high', ct.c_double)],
        'Report': [(name, ct.c_int) for name in ('abi', 'struct_size', 'semantic', 'error_selector')]
                  + [(name, ct.c_uint64) for name in types.WORK],
        'Request': [('steps', ct.c_int), ('values', ct.c_int), ('step', ct.POINTER(types.Step)),
                    ('value', types.D), ('status', types.I), ('errors', types.I), ('output', types.D),
                    ('flags', types.I), ('order', types.I), ('count', types.I)]}
    return all(_field_rows(vars(kind).get('_fields_'), expected[name])
               for name, kind in zip(('Step', 'Request', 'Report'), classes))


def _field_rows(current, expected):
    if type(current) is not list or len(current) != len(expected):
        return False
    return all(type(row) is tuple and len(row) == 2 and type(row[0]) is str and row[0] == name
               and row[1] is value for row, (name, value) in zip(current, expected))


def _aliases_original():
    from . import native_audio_prepare as api, native_audio_prepare_tape as tape
    from . import native_audio_prepare_owner as owner, native_binding, native_filter_prepare_metadata
    from . import native_programmer_transaction as transaction
    rows = ((api, 'bind_native', native_binding.bind_native), (tape, 'number', native_filter_prepare_metadata.number),
            (owner, 'change', transaction.change), (owner, 'commit', transaction.commit),
            (owner, 'Guard', guard.Guard), (owner, 'Tape', tape.Tape))
    return (dependencies.standard_original() and all(vars(module).get(name) is value for module, name, value in rows)
            and all(vars(api).get(name) is vars(types)[name] for name in
                    ('Binding', 'Packed', 'Report', 'Request', 'Result', 'Step', 'D', 'I', 'WORK', 'array', 'report'))
            and all(vars(module).get('ct') is ct for module in (api, tape, types, dependencies))
            and _ctypes_original() and dependencies.supplier_aliases() and _domain_original() and _failure_original(api))


def _failure_original(api):
    kind = vars(api).get('NativeFailure')
    return (type(kind) is type and kind.__bases__ == (RuntimeError,)
            and kind.__init__ is RuntimeError.__init__ and kind.__new__ is RuntimeError.__new__)


def _domain_original():
    plain = (bool, int, float, str, bytes, bytearray, memoryview, type(None))
    containers = (list, tuple, dict, set, frozenset)
    return (type(PLAIN) is tuple and len(PLAIN) == len(plain) and type(CONTAINERS) is tuple
            and len(CONTAINERS) == len(containers) and all(left is right for left, right in zip(PLAIN, plain))
            and all(left is right for left, right in zip(CONTAINERS, containers)))


def _early_globals():
    own = tuple(sys.modules[__package__ + '.' + name] for name in guard.NEW_NAMES)
    for module in own:
        for name in guard.NEW_NAMES[module.__name__.split('.')[-1]]:
            target = module if '.' not in name else vars(module).get(name.split('.')[0])
            if target is not module and type(target) is not type:
                return False
            function = vars(target).get(name.split('.')[-1])
            if type(function) is not type(preflight) or function.__globals__ is not vars(module):
                return False
    return True


def preflight():
    if type(_early_globals) is not type(preflight) or _early_globals.__globals__ is not vars(sys.modules[__name__]):
        return False
    if not _early_globals() or not dependencies.preflight() or not _aliases_original():
        return False
    own, sources = guard.modules()
    return (guard._function_rows((*own, *sources), {}) is not None
            and guard._no_shadow((*own, *sources)) and guard._stdlib_original(sources)
            and guard._source_data((*own, *sources)) is not None)


def current(owner):
    source = _owner_guard(owner)
    if source is None or not _functions_current(source.functions + source.wrapper_rows):
        return False
    if not all(sys.modules.get(name) is captured for name, captured in source.modules):
        return False
    if vars(owner).get('originals') is not source.originals or vars(owner).get('wrappers') is not source.wrappers:
        return False
    return _aliases_original() and source.sources_current() and source.binding_current()


def _owner_guard(owner):
    from . import native_audio_prepare_owner as module
    token = module.SCOPE_TOKEN
    if type(token) is not tuple or len(token) != 2 or token[0] is not owner:
        return None
    source = token[1]
    if type(owner) is not module.Owner or type(source) is not guard.Guard:
        return None
    plain = module.Owner.__getattribute__ is object.__getattribute__ and guard.Guard.__getattribute__ is object.__getattribute__
    valid = vars(owner).get('guard') is source and module._OWNER is owner
    return source if plain and valid else None


def _functions_current(rows):
    for metadata, cells in rows:
        function, code, defaults, keyword, _items = metadata
        if type(function) is not FunctionType or function.__code__ is not code:
            return False
        if function.__defaults__ is not defaults or function.__kwdefaults__ is not keyword:
            return False
        if any(cell.cell_contents is not value for cell, value in cells):
            return False
    return all(_keywords_current(metadata[0].__kwdefaults__, metadata[4]) for metadata, _cells in rows)


def _keywords_current(keyword, entries):
    if entries is None:
        return keyword is None
    if type(keyword) is not dict or len(keyword) != len(entries):
        return False
    return all(left is right and value is original
               for (left, value), (right, original) in zip(keyword.items(), entries))


def bootstrap():
    from . import native_audio_prepare_owner as owner
    keys = ('type', 'len', 'vars', 'all', 'any', 'getattr', 'isinstance', 'FunctionType')
    return tuple((vars(module), name, vars(module).get(name, guard.MISSING))
                 for module in (sys.modules[__name__], guard, owner) for name in keys)
