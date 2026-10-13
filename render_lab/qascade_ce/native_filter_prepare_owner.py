"""Retained default owner and one reversible alias installation; no renderer."""
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import RLock, local
import sys
import warnings
import _warnings
import weakref

from . import native_filter_prepare as api
from . import native_filter_prepare_metadata as metadata
from .native_filter_prepare_guard import Guard
from .native_programmer_transaction import change, commit, RollbackFailure

_LOCK = RLock()
_LOCAL = local()
_OWNER = None
_UNAVAILABLE = False
_COMPAT = ContextVar('qascade_filter_prepare_compatibility', default=False)
SCOPE_RESOLVER = None


@dataclass(frozen=True)
class Owner:
    binding: object
    originals: dict
    wrappers: dict
    guard: object


def _original(owner, name, arguments):
    api.COUNTERS['foreign'] += 1
    return _compatibility(owner, name, arguments)


def _compatibility(owner, name, arguments):
    token = _COMPAT.set(True)
    try:
        return owner.originals[name](*arguments)
    finally:
        _COMPAT.reset(token)


def _memo(owner, clip, values):
    cache = getattr(_LOCAL, 'parameters', None)
    if cache is None:
        cache = _LOCAL.parameters = OrderedDict()
    key, stamp = id(clip), metadata.signature(values)
    cached = cache.get(key)
    if cached is not None and cached[0]() is clip and cached[1] is owner.binding and cached[2] == stamp:
        api.COUNTERS['parameter_cache_hits'] += 1
        cache.move_to_end(key)
        return cached[3]
    result = api.parameters(owner.binding, values)
    cache[key] = (weakref.ref(clip), owner.binding, stamp, result)
    cache.move_to_end(key)
    if len(cache) > 128:
        cache.popitem(last=False)
    return result


def _params(owner, clip):
    if _UNAVAILABLE:
        raise RuntimeError('Filter preparation metadata rollback failed')
    if not owner.guard.current():
        return None
    values = metadata.clip_values(clip, owner.guard.clip_type)
    return None if values is None else _memo(owner, clip, values)


def _parameters(clip):
    owner = _OWNER
    try:
        result = _params(owner, clip)
    except RuntimeError:
        api.COUNTERS['fallbacks'] += 1
        return _compatibility(owner, 'clip_filter_params', (clip,))
    if result is None:
        return _original(owner, 'clip_filter_params', (clip,))
    values = result[0]
    return values[0], values[1], values[2]


def _active(clip):
    owner = _OWNER
    try:
        result = _params(owner, clip)
    except RuntimeError:
        api.COUNTERS['fallbacks'] += 1
        return _compatibility(owner, 'filters_active', (clip,))
    return _original(owner, 'filters_active', (clip,)) if result is None else result[1]


def _span_source(owner, engine, universe, project):
    if not owner.guard.current() or not owner.guard.numpy_current() or warnings.warn is not _warnings.warn:
        return False
    if type(universe) not in (int, bool):
        return False
    return metadata.warm_source_safe(engine, project, owner.guard.types[0])


def _span_math(owner, engine, universe, project):
    public = owner.guard.public
    cache, key, hit = public._mask_cache(engine, '__output_filter_span__', universe, project)
    if hit is not None:
        api.COUNTERS['span_cache_hits'] += 1
        return hit
    if not metadata.source_safe(project, owner.guard.types):
        return _original(owner, 'universe_deg_spans', (engine, universe, project))
    axes = tuple(public.MOTOR_COARSE_CHANNELS)
    rows = metadata.span_rows(public, project, universe, axes)
    result = api.spans(owner.binding, rows, axes)
    public._mask_store(engine, cache, key, project, result)
    return result


def _spans(engine, universe, proj):
    owner = _OWNER
    project = proj
    if not _span_source(owner, engine, universe, project):
        return _original(owner, 'universe_deg_spans', (engine, universe, project))
    try:
        return _span_math(owner, engine, universe, project)
    except RuntimeError:
        api.COUNTERS['fallbacks'] += 1
        return _compatibility(owner, 'universe_deg_spans', (engine, universe, project))


def _wrapped(function, original):
    from . import native_filter_prepare_scope as scope_module
    admission = scope_module.owner_current
    retained = (admission.__code__, admission.__defaults__, admission.__kwdefaults__)
    compatibility = _COMPAT
    @wraps(original)
    def wrapper(*args, **kwargs):
        if _UNAVAILABLE:
            raise RuntimeError('Filter preparation metadata rollback requires recovery')
        if _OWNER is None or compatibility.get():
            return original(*args, **kwargs)
        valid = scope_module.owner_current is admission and all(value is captured for value, captured in
                  zip((admission.__code__, admission.__defaults__, admission.__kwdefaults__), retained))
        if valid:
            valid = admission(_OWNER)
        if not valid:
            api.COUNTERS['foreign'] += 1
            token = compatibility.set(True)
            try:
                return original(*args, **kwargs)
            finally:
                compatibility.reset(token)
        return function(*args, **kwargs)
    return wrapper


def original_for(name, function):
    owner = _OWNER
    if _UNAVAILABLE or owner is None or name not in owner.wrappers or function is not owner.wrappers[name]:
        return function
    return owner.originals[name] if owner.guard.current() else function


def _prepare_install(context):
    public, playback, clips = context
    from . import native_filter_prepare_guard as guard_module, native_filter_prepare_types as types_module
    from . import native_filter_prepare_scope as scope_module
    names = ('clip_filter_params', 'filters_active', 'universe_deg_spans')
    if _OWNER is None:
        originals = {name: getattr(public, name) for name in names}
        implementations = (_parameters, _active, _spans)
        wrappers = {name: _wrapped(function, originals[name]) for name, function in zip(names, implementations)}
    else:
        originals, wrappers = _OWNER.originals, _OWNER.wrappers
    modules = (api, types_module, metadata, guard_module, sys.modules[__name__], scope_module)
    return originals, wrappers, Guard(context, wrappers, originals, modules)


def _install_changes(owner, context):
    from . import native_filter_prepare_scope as scope_module
    public, playback, clips = context
    changes = [change(public, name, wrapper) for name, wrapper in owner.wrappers.items()]
    changes.extend((change(playback, 'clip_filter_params', owner.wrappers['clip_filter_params']),
                    change(clips, 'filters_active', owner.wrappers['filters_active'])))
    admission = scope_module.owner_current
    admission_metadata = (admission, admission.__code__, admission.__defaults__, admission.__kwdefaults__)
    resolver = (original_for, original_for.__code__, original_for.__defaults__, original_for.__kwdefaults__, owner, admission_metadata)
    changes.extend((change(api, 'PARAMETERS_FUNCTION', owner.binding.parameters), change(api, 'SPANS_FUNCTION', owner.binding.spans),
                    change(sys.modules[__name__], '_OWNER', owner), change(sys.modules[__name__], 'SCOPE_RESOLVER', resolver)))
    return changes


def _install_current():
    from .native_filter_prepare_scope import preflight
    return preflight() and (_OWNER is None or _OWNER.guard.current())


def install(engine):
    global _UNAVAILABLE
    from src.engine import clip_output_filters as public, playback_engine as playback, clip_frame_evaluation as clips
    if _UNAVAILABLE:
        raise RuntimeError('Filter preparation metadata rollback requires recovery')
    with _LOCK:
        if not _install_current():
            api.COUNTERS['foreign'] += 1
            return False
        if _OWNER is not None and _OWNER.binding.engine is engine:
            return True
        originals, wrappers, guard = _prepare_install((public, playback, clips))
        if not guard.safe:
            api.COUNTERS['foreign'] += 1
            return False
        owner = Owner(api.prepare_binding(engine), originals, wrappers, guard)
        guard.attach(owner)
        changes = _install_changes(owner, (public, playback, clips))
        try:
            commit(changes)
        except RollbackFailure:
            _UNAVAILABLE = True
            raise
    return True
