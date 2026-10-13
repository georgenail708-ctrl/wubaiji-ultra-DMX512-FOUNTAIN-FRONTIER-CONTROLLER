"""Only registered exact software K2 proxies may enter existing C03 math."""
from threading import RLock
from weakref import ref
import sys

from . import native_fade_owned_schema as schema


def _functions_current(rows):
    return all(schema.same_function(schema._locate(module, name), row) for module, name, row in rows)


def _values_current(rows):
    return all(namespace.get(name) is value for namespace, name, value in rows)


def _core_current(source, wrappers):
    core, class_rows, methods, functions, values, kind, _old_modules, old_rows, entry = source.core
    if type(core) is not kind or kind.__getattribute__ is not object.__getattribute__ or core.class_rows is not class_rows:
        return False
    if not _core_methods(methods, kind) or not _core_class_rows(class_rows, methods):
        return False
    return _core_functions(functions, entry, wrappers[0]) and _module_rows(core, old_rows, entry, wrappers[2]) and _values_current(values)


def _core_methods(methods, kind):
    return all(schema.same_function(row[0], row) and vars(kind).get(name) is row[0]
               for name, row in methods)


def _core_class_rows(class_rows, methods):
    return (type(class_rows) is dict and len(class_rows) == len(methods)
            and all(class_rows.get(name) is row for name, row in methods))


def _core_functions(functions, entry, wrapper):
    for namespace, name, row in functions:
        if namespace is entry[0] and name == entry[1]:
            if namespace.get(name) is not wrapper or not schema.same_function(row[0], row):
                return False
        elif not schema.same_function(namespace.get(name), row):
            return False
    return True


def _module_rows(core, old, entry, bridge):
    current, replacement = bridge
    if core.module_rows is not current or type(current) is not list or len(current) != len(old):
        return False
    return all(actual is (replacement if previous is entry else previous) for actual, previous in zip(current, old))


def _proxy_current(source, wrappers):
    kind, methods, fields, builtin_rows, package = source.proxy
    if vars(package).get('_K2FrameCacheNetworkProxy') is not kind or kind.__getattribute__ is not object.__getattribute__:
        return False
    members = all(schema.same_function(row[0], row) and vars(kind).get(name) is (wrappers[1] if name == '__init__' else row[0])
                  for name, row in methods)
    return (members and _wrappers_current(wrappers) and all(vars(kind).get(name) is value for name, value in fields)
            and _values_current(builtin_rows))


def _wrappers_current(wrappers):
    return all(schema.wrapper_current(function, metadata, wrappers) for function, metadata in zip(wrappers[:2], wrappers[3]))


def retained(source, registry, wrappers):
    from .native_fade_owned_install import _OWNER
    if type(_OWNER) is not schema.Owner or schema.Owner.__getattribute__ is not object.__getattribute__:
        return False
    if vars(_OWNER).get('source') is not source or vars(_OWNER).get('registry') is not registry or vars(_OWNER).get('wrappers') is not wrappers:
        return False
    if vars(sys.modules[__package__ + '.native_fade_target_install']).get('_GUARD') is not source.core[0]:
        return False
    functions, globals_rows, modules = source.own
    return (_functions_current(functions) and _values_current(globals_rows) and schema.own_original()
            and all(sys.modules.get(name) is module for name, module in modules)
            and _core_current(source, wrappers) and _proxy_current(source, wrappers))


def registry():
    return {}, RLock()


def _retire(store, key, reference):
    rows, lock = store
    with lock:
        previous = rows.get(key)
        if previous is not None and previous[0] is reference:
            rows.pop(key)


def remember(store, proxy, target):
    rows, lock = store
    key = id(proxy)
    retire, code = _retire, _retire.__code__
    def callback(reference):
        if retire.__code__ is code and retire.__defaults__ is None and retire.__kwdefaults__ is None:
            retire(store, key, reference)
    reference = ref(proxy, callback)
    with lock:
        rows[key] = reference, target


def _target(source, store, owner):
    core = source.core[0]
    proxy = vars(owner).get('_nm')
    if type(proxy) is not source.proxy[0] or vars(proxy).get('_build_mode') is not True:
        return None
    if '_channel_defaults' in vars(proxy) or '_target' in vars(type(proxy)):
        return None
    target = _registered_target(store, proxy)
    return target if type(target) is core.nm else None


def _registered_target(store, proxy):
    rows, lock = store
    with lock:
        entry = rows.get(id(proxy))
        if type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not ref:
            return None
        if entry[0]() is not proxy or vars(proxy).get('_target') is not entry[1]:
            return None
        return entry[1]


def _owned(owner):
    values = vars(owner)
    return (values.get('_timeline_bake_owned') is True
            or (values.get('_timeline_render_cache_building_lock') is True
                and values.get('_suppress_position_emit') is True
                and values.get('_timeline_render_cache_locked') is False))


def guard_for(source, store, wrappers, arguments):
    from .native_fade_owned_install import _OWNER
    from src.engine.playback_engine import PlaybackEngine
    from src.network.network_manager import NetworkManager
    owner, universe, project = arguments
    core = source.core[0]
    if (type(_OWNER) is not schema.Owner or schema.Owner.__getattribute__ is not object.__getattribute__
            or type(owner) is not core.owner or core.owner.__getattribute__ is not object.__getattribute__):
        return None
    nm = vars(owner).get('_nm')
    if type(nm) is NetworkManager and core.nm is NetworkManager and core.owner is PlaybackEngine:
        packet = vars(_OWNER)
        original = source.core[8][2][0]
        valid = all((packet.get('source') is source, packet.get('registry') is store, packet.get('wrappers') is wrappers,
                     vars(sys.modules[__package__ + '.native_fade_target_install']).get('_GUARD') is core,
                     source.core[8][0].get(source.core[8][1]) is wrappers[0],
                     schema.same_function(original, source.core[8][2]),
                     schema.wrapper_current(wrappers[0], wrappers[3][0], wrappers)))
        return original(owner, universe, project) if valid else None
    if not retained(source, store, wrappers):
        return None
    if type(nm) is not source.proxy[0]:
        return source.core[8][2][0](owner, universe, project)
    target = _target(source, store, owner) if _owned_context(core, arguments) else None
    from .native_fade_target_guard import plain_defaults
    return core if target is not None and plain_defaults(target, universe) else None


def _owned_context(core, arguments):
    owner, universe, project = arguments
    return (_owned(owner) and core._classes_current() and core._owner_current(owner, universe)
            and core._project_current(project) and core.source_current() and core.binding_current())
