"""Original public ownership and one reversible alias transaction; no renderer."""
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import RLock
import importlib
import sys

from . import native_audio_prepare as api, native_audio_prepare_scope as scope
from . import native_audio_prepare_points as points, native_audio_prepare_processors as processors
from . import native_audio_prepare_clips as clips, native_audio_prepare_state as state_plan
from .native_audio_prepare_tape import Tape
from .native_audio_prepare_guard import Guard, MISSING
from .native_programmer_transaction import change, commit, RollbackFailure

_OWNER = None
_UNAVAILABLE = False
SCOPE_TOKEN = None
_LOCK = RLock()
_COMPAT = ContextVar('qascade_audio_prepare_compatibility', default=False)
ROLES = dict(processors=('src.engine.audio_mod_processor_schema', 'normalize_processors'),
             state=('src.engine.audio_modulation', 'normalize_state'),
             points=('src.engine.audio_mod_envelope', 'normalize_points'),
             degree=('src.engine.audio_mod_spread', 'clamp_deg'))


@dataclass(frozen=True)
class Owner:
    binding: object
    originals: object
    wrappers: object
    guard: object


def _original(role, arguments):
    owner = _OWNER
    api.COUNTERS['foreign'] += 1
    token = _COMPAT.set(True)
    try:
        return owner.originals[ROLES[role]](*arguments)
    finally:
        _COMPAT.reset(token)


def _processors(raw):
    if not scope.ordinary(raw):
        return _original('processors', (raw,))
    tape = Tape()
    public = _OWNER.guard.sources['src.engine.audio_mod_processor_schema']
    prepared = processors.prepare(tape, raw, public)
    return processors.publish(api.execute(_OWNER.binding, 'PROCESSORS', tape), prepared)


def _points(raw):
    if not scope.ordinary(raw):
        return _original('points', (raw,))
    tape = Tape()
    prepared = points.prepare(tape, raw)
    return points.publish(api.execute(_OWNER.binding, 'POINTS', tape), prepared)


def _state(state):
    if not scope.ordinary(state):
        return _original('state', (state,))
    tape = Tape()
    sources = _OWNER.guard.sources
    suppliers = tuple(sources[name] for name in
                      ('src.engine.audio_modulation', 'src.engine.audio_mod_processor_schema', 'src.engine.audio_mod_lifetime'))
    prepared = state_plan.prepare(tape, state, suppliers)
    return state_plan.publish(api.execute(_OWNER.binding, 'STATE', tape), prepared)


def _degree(v):
    if not scope.ordinary(v):
        return _original('degree', (v,))
    tape = Tape()
    index = tape.add('CLAMP', (v,), (0., 0., 360., 0))
    return api.execute(_OWNER.binding, 'STATE', tape).scalar(index)


def _wrapped(function, original):
    check, compatibility = scope.current, _COMPAT
    code, defaults, keyword = check.__code__, check.__defaults__, check.__kwdefaults__
    bootstrap = scope.bootstrap()
    fold, pairs = all, zip
    @wraps(original)
    def wrapper(*args, **kwargs):
        if _UNAVAILABLE:
            raise RuntimeError('Audio preparation rollback requires recovery')
        if _OWNER is None or compatibility.get():
            return original(*args, **kwargs)
        actual = (scope.current, check.__code__, check.__defaults__, check.__kwdefaults__)
        valid = fold(value is expected for value, expected in pairs(actual, (check, code, defaults, keyword)))
        for namespace, name, value in bootstrap:
            if namespace.get(name, MISSING) is not value:
                valid = False
                break
        if valid:
            valid = check(_OWNER)
        if not valid:
            api.COUNTERS['foreign'] += 1
            token = compatibility.set(True)
            try:
                return original(*args, **kwargs)
            finally:
                compatibility.reset(token)
        return _execute_owned(function, original, (args, kwargs), compatibility)
    return wrapper


def _execute_owned(function, original, arguments, compatibility):
    args, kwargs = arguments
    try:
        return function(*args, **kwargs)
    except api.NativeFailure:
        api.COUNTERS['fallbacks'] += 1
        token = compatibility.set(True)
        try:
            return original(*args, **kwargs)
        finally:
            compatibility.reset(token)


def _alias_rows(wrappers):
    public = [(importlib.import_module(module), name, wrappers[role]) for role, (module, name) in ROLES.items()]
    state = importlib.import_module('src.engine.audio_modulation')
    processor_state = importlib.import_module('src.engine.audio_mod_processor_state')
    folder = importlib.import_module('src.engine.audio_mod_folder_scope')
    public.extend(((state, 'normalize_points', wrappers['points']), (state, 'clamp_deg', wrappers['degree']),
                   (processor_state, 'normalize_processors', wrappers['processors']), (folder, 'normalize_state', wrappers['state'])))
    return tuple(public)


def _prepare_install():
    if _OWNER is None:
        originals = {(module, name): vars(importlib.import_module(module))[name] for module, name in ROLES.values()}
        implementations = dict(processors=_processors, state=_state, points=_points, degree=_degree)
        wrappers = {role: _wrapped(implementations[role], originals[key]) for role, key in ROLES.items()}
    else:
        originals, wrappers = _OWNER.originals, _OWNER.wrappers
    aliases = _alias_rows(wrappers)
    guard = Guard(originals, wrappers, aliases)
    return originals, wrappers, aliases, guard


def _install_ready():
    if _OWNER is None:
        return scope.preflight()
    return scope.current(_OWNER)


def _changes(owner, aliases):
    rows = [change(module, name, value) for module, name, value in aliases]
    rows.extend(change(api, name + '_FUNCTION', owner.binding.functions[name]) for name in ('PROCESSORS', 'STATE', 'POINTS', 'ROUND'))
    rows.extend((change(sys.modules[__name__], '_OWNER', owner),
                 change(sys.modules[__name__], 'SCOPE_TOKEN', (owner, owner.guard))))
    return rows


def _admitted_engine(engine):
    from .native_audio_prepare_dependencies import engine_original
    if _install_ready() and engine_original(engine):
        return True
    api.COUNTERS['foreign'] += 1
    return False


def install(engine):
    global _UNAVAILABLE
    if _UNAVAILABLE:
        raise RuntimeError('Audio preparation rollback requires recovery')
    with _LOCK:
        if not _admitted_engine(engine):
            return False
        if _OWNER is not None and _OWNER.binding.engine is engine:
            return True
        originals, wrappers, aliases, guard = _prepare_install()
        if not guard.safe:
            api.COUNTERS['foreign'] += 1
            return False
        owner = Owner(api.prepare_binding(engine), originals, wrappers, guard)
        guard.attach(owner)
        try:
            commit(_changes(owner, aliases))
        except RollbackFailure:
            _UNAVAILABLE = True
            raise
    return True
