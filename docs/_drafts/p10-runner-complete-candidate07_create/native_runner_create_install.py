"""One reviewed R02→R07 installation before owners, real public add consumers."""
import sys
from . import native_runner_edit_install as previous
from . import native_runner_create_guard as guard_module
from . import native_runner_create_binding as binding
from . import native_runner_create_args as arguments
from . import native_runner_create_model as model
from . import native_runner_create_adopt as adopt
from . import native_runner_create_state as state
from . import native_runner_create_runtime as runtime
from . import native_runner_create_dispatch as dispatch
from . import native_runner_create_wrapper as wrappers
from . import native_runner_create_types as types
from . import native_runner_create_consumers as consumers

_GUARD = None


def _modules():
    edit = (previous.guard_module,previous.binding,previous.model,previous.adopt,
        previous.runtime,previous.types,previous)
    create = (guard_module,binding,arguments,model,adopt,state,runtime,dispatch,wrappers,types,consumers,sys.modules[__name__])
    return edit,create


def install(engine):
    global _GUARD
    retirement = previous.previous
    base = retirement.previous.base_install._PROOF
    if not retirement._ready(base) or previous._GUARD is not None or _GUARD is not None:
        return False
    scopes,registers,critical,reviewed = retirement._modules()
    edit,create = _modules()
    if not _qualified(edit,create,reviewed):
        return False
    compat = dispatch.prepare_compat()
    try:
        cleanup_source = retirement.source.Source(critical)
        guard = guard_module.Guard(base,((scopes,registers),reviewed),edit+create,cleanup_source)
        if not guard.safe:
            dispatch.discard_compat(compat)
            return False
        entry_binding = binding.Binding(engine)
        if not entry_binding.current() or entry_binding.functions['install_ready']() != 1:
            dispatch.discard_compat(compat)
            return False
        record = _record(guard,entry_binding,cleanup_source)
        previous._publish(guard,record)
    except BaseException:
        dispatch.discard_compat(compat)
        raise
    _set_guards(guard)
    return True


def _qualified(edit,create,reviewed):
    return (previous._qualified(edit,reviewed) and guard_module.providers_current()
        and all(guard_module.canonical(module) and guard_module.canonical_classes(module) for module in create)
        and guard_module.layouts_current(types))


def _record(guard,entry_binding,cleanup_source):
    retirement = previous.previous
    admit,freeze_admit = retirement.scope_guard.make_admit(guard)
    train_admit,freeze_train = guard_module.make_train_admit(guard,entry_binding)
    context = guard,entry_binding,admit,train_admit
    old_context = context[:3]
    operations = retirement.previous._operations() | {'_ensure_legacy_track':dispatch.ensure,
        'set_path':previous.runtime.set_path,'attach_train_to_track':dispatch.attach}
    originals = retirement.previous._originals(guard.base,operations)
    retirement.previous._replacements(guard,old_context,operations,originals)
    retirement.previous._additions(guard,old_context)
    guard.record_addition('attach_train_materialized',retirement.scope_install._wrapper(
        old_context,previous.runtime.panel_attach,previous.runtime.panel_attach))
    _record_create(guard,context,originals)
    guard.record_addition('add_train_materialized',wrappers.wrapper(context,dispatch.panel,dispatch.panel))
    retirement._record_host(guard,retirement._host_methods(cleanup_source))
    guard.seal()
    freeze_admit()
    freeze_train()
    return context,originals


def _record_create(guard,context,originals):
    for name,operation in (('_add_train_v2',dispatch.v2),('_add_train_legacy',dispatch.legacy)):
        original = vars(guard.base.owner)[name]
        originals[name] = original
        guard.record_replacement(name,original,wrappers.wrapper(context,original,operation))
    kind = guard.base.module._Train
    original = vars(kind)['__init__']
    guard.record_host(guard.base.module,(kind,'__init__'),original,
        wrappers.wrapper(context,original,dispatch.constructor,True))


def _set_guards(guard):
    global _GUARD
    retirement = previous.previous
    retirement.scope_install._GUARD = retirement.previous._GUARD = retirement._GUARD = previous._GUARD = _GUARD = guard
