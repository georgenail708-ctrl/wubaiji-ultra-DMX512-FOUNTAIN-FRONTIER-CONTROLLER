"""Install one exact class wrapper, retaining frozen old source and metadata."""
_GUARD = None


def guard_for(owner,universe,proj):
    guard = _GUARD
    return guard if guard is not None and guard.current(owner,universe,proj) else None


def wrapper_current(function,owner,instance=None):
    """Additional admission for live-scope _EXTRA, before its old unwrap/hash."""
    guard = _GUARD
    return (guard is not None and owner is guard.owner and function is guard.wrapper
        and (instance is None or (type(instance) is owner
            and not any(name in vars(instance) for name in guard.originals)))
        and guard.source_current() and guard.binding_current())


def install(engine):
    global _GUARD
    if _GUARD is not None:
        return _GUARD.source_current() and _GUARD.binding_current()
    from . import native_fade_target as math
    from .native_fade_target_guard import FadeGuard
    from .native_fade_target_runtime import wrap
    from .native_fade_target_schema import preflight
    # Metadata qualification precedes even the canonical DLL binder.
    if not preflight(engine):
        return False
    if not math.install(engine):
        return False
    guard = FadeGuard(engine)
    if not guard.safe:
        return False
    wrapper = wrap(guard.original)
    if not guard.attach(wrapper):
        return False
    _GUARD = guard
    guard.owner._get_fade_out_target = wrapper
    return True
