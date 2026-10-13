"""One exact entry/constructor transaction; every original C03 method is retained."""
from functools import wraps
import sys

from . import native_fade_owned_schema as schema, native_fade_owned_scope as scope
from .native_programmer_transaction import change, commit, RollbackFailure

_OWNER = None
_BROKEN = False


def _fields():
    names = ('core', 'proxy', 'own', 'source', 'registry', 'wrappers', '__getattribute__', '__new__')
    return tuple((kind, name, vars(kind).get(name)) for kind in (schema.Source, schema.Owner) for name in names)


def _bootstrap(rows, globals_rows, fields, packet):
    source, expected = packet
    if not (type(source) is schema.Source and schema.Source.__getattribute__ is object.__getattribute__
            and all(vars(kind).get(name) is value for kind, name, value in fields)
            and all(vars(source).get(name) is value for name, value in expected)):
        return False
    for module, name, row in rows:
        pieces = name.split('.')
        target = module if len(pieces) == 1 else vars(module).get(pieces[0])
        if target is not module and type(target) is not type:
            return False
        actual = vars(target).get(pieces[-1])
        if not (type(actual) is type(_bootstrap) and actual is row[0] and actual.__code__ is row[1]
                and actual.__defaults__ is row[2] and actual.__kwdefaults__ is row[3]):
            return False
    return all(namespace.get(name) is value for namespace, name, value in globals_rows)


def _wrap(source, store):
    original, constructor = source.core[8][2][0], source.proxy[1][0][1][0]
    rows, globals_rows, _modules = source.own
    fields, packet = _fields(), (source, tuple(vars(source).items()))
    boot, boot_code = _bootstrap, _bootstrap.__code__
    wrappers = None
    @wraps(original)
    def guard_for(owner, universe, proj):
        if _bootstrap is not boot or boot.__code__ is not boot_code or not boot(rows, globals_rows, fields, packet):
            return None
        return scope.guard_for(source, store, wrappers, (owner, universe, proj))
    @wraps(constructor)
    def proxy_init(instance, target, build_mode=False):
        constructor(instance, target, build_mode=build_mode)
        if _bootstrap is not boot or boot.__code__ is not boot_code or not boot(rows, globals_rows, fields, packet):
            return
        if scope.retained(source, store, wrappers) and type(instance) is source.proxy[0] and type(target) is source.core[0].nm:
            scope.remember(store, instance, target)
    replacement = (source.core[8][0], source.core[8][1], schema.metadata(guard_for))
    module_rows = [replacement if row is source.core[8] else row for row in source.core[7]]
    metadata = schema.wrapper_metadata(guard_for), schema.wrapper_metadata(proxy_init)
    wrappers = guard_for, proxy_init, (module_rows, replacement), metadata
    return wrappers


def _changes(owner):
    source = owner.source
    installer = sys.modules[__package__ + '.native_fade_target_install']
    return (change(installer, 'guard_for', owner.wrappers[0]),
            change(source.proxy[0], '__init__', owner.wrappers[1]),
            change(source.core[0], 'module_rows', owner.wrappers[2][0]),
            change(sys.modules[__name__], '_OWNER', owner))


def install(engine):
    global _BROKEN
    if _BROKEN:
        raise RuntimeError('Owned fade admission rollback requires recovery')
    if _OWNER is not None:
        return _OWNER.source.core[0].engine is engine and scope.retained(_OWNER.source, _OWNER.registry, _OWNER.wrappers)
    from .native_fade_target_install import _GUARD
    if _GUARD is None or _GUARD.engine is not engine:
        return False
    source = schema.create(_GUARD)
    if source is None:
        return False
    store = scope.registry()
    owner = schema.Owner(source, store, _wrap(source, store))
    try:
        commit(_changes(owner))
    except RollbackFailure:
        _BROKEN = True
        raise
    return True
