"""Draft: causal motor seam anchors using original selector/sample/native filter.

No product mount. ROOT reviews and runs independent original math contracts.
Source qualification uses the existing CPython metadata signature primitive;
the existing 24/44 source stamps are unchanged. No mutable frame/mask hash memo.
"""
from __future__ import annotations

import hashlib
import sys

from . import clip_output_filters as filters
from . import fade_anchor_invalidation as anchor_cache

STATE = "_transition_anchor_causal_state"
POLICY = "_transition_anchor_causal_policy"
REVISION = "_transition_anchor_causal_revision"
_DEFAULTS = {
    "_get_fade_in_anchor": (None, None),
    "_find_previous_transition_clips": (None, None),
    "_build_cross_track_fade_in_anchor": (None, None, None),
    "_sample_transition_clip_output": (None, None, False),
    "_fade_in_anchor_ready_context": (None, None),
    "_transition_candidate_signature": None,
    "_fade_in_anchor_dependency_key": None,
}
EXPECTED = {'_fade_in_anchor_dependency_key': '1ef8d257027bdb94d6c2e0ea31a956cf10000732c8fc88e56a84581fbde4883f', '_fade_in_anchor_ready_context': '6fb033160af131b8c1fbcf7fb12307f9a101dcdf48ade7068d5960fd0afa2295', '_transition_candidate_signature': '77ce955772db0a1bf88ffbd964af613841c9a23e81f76f6079b4cf02d5c1bcf5', '_find_previous_transition_clips': '15008d6892be94b60cc85fdeae9b77d1938936ac13df5966f10d31bac6799be2', '_build_cross_track_fade_in_anchor': '64aa43cbdb9c96d7d3cc0d41558f4f3eac98db6e8a02802f5731e5142b888773', '_get_fade_in_anchor': '8bd041f1876f3574ab34fbf19a0e4363adc6bd05f211ee08ce95f97822fa6f5a', '_sample_transition_clip_output': 'd744b4e76553ef2b5c5b0082bb78fa91713326c16a5846a4fd39ffcf34cd1a6e', '_to_float': '3d8f560d9e138e249818542d7b16fb4e4c8a3c62da8fc5df68c0ff8e1a69385a', '_clamp01': '61a379d6af9474b1065712a39f027643e4518f01be8647a6014cdfedd0a1f933', 'clip_filter_params': '23f5545460c2c9b789075b5c78057019060ebaae43bb6a7b9e9659c0aed420d4', 'evict_fade_anchor_cache': 'c9b717a7e5830e8054b5e760950261fa86f44bd3f9d0e8402c9dfed37abc5174'}
_FILTER_DEFAULTS = {"clip_filter_params": None, "_to_float": None, "_clamp01": (1.0,)}
_FILTER_FIELDS = ("motor_limit_scale", "motor_speed_limit_dps", "pump_master_scale")
_PRIMITIVES = (float, int, bool)
_ANCHOR_DEFAULTS = {"evict_fade_anchor_cache": None}
_QUALIFIED = None


def _metadata(function):
    return function, function.__code__, function.__defaults__, function.__kwdefaults__


def _initial_functions(owner):
    from qascade_ce.native_curve_scalar_guard import _signature
    from types import FunctionType
    namespace = vars(sys.modules[owner.__module__])
    functions = dict((name, vars(owner).get(name)) for name in _DEFAULTS)
    from .filter_preparation_scope import original_filter_function
    functions.update((name, original_filter_function(name, vars(filters).get(name))) for name in _FILTER_DEFAULTS)
    functions.update((name, vars(anchor_cache).get(name)) for name in _ANCHOR_DEFAULTS)
    for name, function in functions.items():
        if type(function) is not FunctionType:
            return None
        wanted = _function_namespace(name, namespace)
        defaults = _expected_defaults(name)
        if function.__globals__ is not wanted or not _defaults_current(function, defaults):
            return None
        if function.__kwdefaults__ is not None:
            return None
        digest = hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()
        if not _source_original(name, function, digest):
            return None
    if not _anchor_dispatch_current(owner, functions["evict_fade_anchor_cache"]):
        return None
    return dict((name, _metadata(function)) for name, function in functions.items())


def _source_original(name, function, digest):
    if digest == EXPECTED.get(name):
        return True
    from .native_anchor_preparation import qualified_source
    return qualified_source(name, function)


def _default_value_current(value, wanted):
    if wanted is None or type(wanted) is bool:
        return value is wanted
    return type(value) is type(wanted) and value == wanted


def _defaults_current(function, expected):
    actual = function.__defaults__
    if expected is None:
        return actual is None
    if type(actual) is not tuple or len(actual) != len(expected):
        return False
    return all(_default_value_current(value, wanted) for value, wanted in zip(actual, expected))


def _function_namespace(name, namespace):
    if name in _FILTER_DEFAULTS:
        return vars(filters)
    if name in _ANCHOR_DEFAULTS:
        return vars(anchor_cache)
    return namespace


def _expected_defaults(name):
    if name in _FILTER_DEFAULTS:
        return _FILTER_DEFAULTS[name]
    if name in _ANCHOR_DEFAULTS:
        return _ANCHOR_DEFAULTS[name]
    return _DEFAULTS[name]


def _scope_object(engine, owner):
    return type(engine) is owner and not any(name in vars(engine) for name in _DEFAULTS)


def _anchor_dispatch_current(owner, expected):
    return vars(sys.modules[owner.__module__]).get("evict_fade_anchor_cache") is expected


def _metadata_current(function, previous):
    return (function is previous[0] and function.__code__ is previous[1]
            and function.__defaults__ is previous[2] and function.__kwdefaults__ is previous[3])


def _functions_current(owner, qualified):
    namespace = vars(owner)
    for name, previous in qualified.items():
        function = _function_namespace(name, namespace).get(name)
        if name in _FILTER_DEFAULTS:
            from .filter_preparation_scope import original_filter_function
            function = original_filter_function(name, function)
        if not _metadata_current(function, previous):
            return False
    from .native_anchor_preparation import current
    return (current() and type(filters._EPS) is float and filters._EPS == 1e-6
            and _anchor_dispatch_current(owner, qualified["evict_fade_anchor_cache"][0]))


def default_scope(engine):
    """Exact rewritten/omitted getter dependencies; no foreign equality calls."""
    from .playback_engine import PlaybackEngine
    global _QUALIFIED
    if not _scope_object(engine, PlaybackEngine):
        return False
    if _QUALIFIED is None:
        try:
            _QUALIFIED = _initial_functions(PlaybackEngine)
        except ModuleNotFoundError:
            return False
    return _QUALIFIED is not None and _functions_current(PlaybackEngine, _QUALIFIED)


def _key(clip, universe):
    return str(getattr(clip, "id", "") or id(clip)), int(universe)


def _model_access_current():
    from src.core.models import Clip, Track, Project
    return all(kind.__getattribute__ is object.__getattribute__ for kind in (Clip, Track, Project))


def _primitive_filter_parameters(clip):
    """Raw exact scalars only; never evaluate a descriptor or numeric protocol."""
    from src.core.models import Clip
    if type(clip) is not Clip:
        return False
    defaults = vars(Clip)
    if not all(type(defaults.get(name)) in _PRIMITIVES for name in _FILTER_FIELDS):
        return False
    values = vars(clip)
    return all(type(values.get(name, defaults[name])) in _PRIMITIVES for name in _FILTER_FIELDS)


def _ordinary_inputs(engine, clip, universe, track, proj):
    from src.core.models import Clip, Track, Project
    ordinary = (type(universe) is int and type(clip) is Clip
                and (track is None or type(track) is Track)
                and (proj is None or type(proj) is Project))
    return (ordinary and _model_access_current() and default_scope(engine)
            and _primitive_filter_parameters(clip))


def _candidates(engine, clip, universe, track, proj, *, context):
    key = _key(clip, universe)
    policy = getattr(engine, POLICY, None)
    if policy is None:
        policy = {}
        setattr(engine, POLICY, policy)
    item = policy.get(key)
    if item is None or item[0] != context:
        rows = engine._find_previous_transition_clips(clip, universe, track=track, proj=proj)
        item = context, tuple(rows)
        policy[key] = item
    return item[1]


def _motor_dependent(candidates):
    # Read current public parameters on every access; no stale dps/mask memo.
    return any(filters.clip_filter_params(row[0])[1] > filters._EPS for row in candidates)


def _motor_context(candidates):
    return tuple((_key(row[0], row[5]), filters.clip_filter_params(row[0]))
                 for row in candidates)


def prewarm_allowed(engine, clip, universe, *, track=None, proj=None):
    if not _ordinary_inputs(engine, clip, universe, track, proj):
        return True
    context = engine._fade_in_anchor_ready_context(clip, universe, track=track, proj=proj)
    candidates = _candidates(engine, clip, universe, track, proj, context=context)
    if not all(_primitive_filter_parameters(row[0]) for row in candidates):
        return True
    return not _motor_dependent(candidates)


def prewarm_transition_anchor(engine, clip, universe, *, track=None, proj=None):
    if prewarm_allowed(engine, clip, universe, track=track, proj=proj):
        return engine._get_fade_in_anchor(clip, universe, track=track, proj=proj)
    return None


def legacy_anchor_allowed(engine, clip, universe, *, track=None, proj=None):
    if not _ordinary_inputs(engine, clip, universe, track, proj):
        return True
    # The static table has no per-clip rate history/filter execution stage.
    if filters.clip_filter_params(clip)[1] > filters._EPS:
        return False
    return prewarm_allowed(engine, clip, universe, track=track, proj=proj)


def _same_anchor_entry(engine, entry, context, motor_context):
    from .transition_anchor_geometry import source_geometry_matches
    return (entry is not None and (entry["context"] == context
            or source_geometry_matches(engine, entry, context))
            and entry["motor_context"] == motor_context)


def causal_anchor(engine, clip, universe, *, track=None, proj=None):
    if not _ordinary_inputs(engine, clip, universe, track, proj):
        return None
    context = engine._fade_in_anchor_ready_context(clip, universe, track=track, proj=proj)
    candidates = _candidates(engine, clip, universe, track, proj, context=context)
    if not all(_primitive_filter_parameters(row[0]) for row in candidates):
        return None
    if not _motor_dependent(candidates):
        return None
    key = _key(clip, universe)
    motor_context = _motor_context(candidates)
    state = getattr(engine, STATE, {})
    entry = state.get(key)
    if _same_anchor_entry(engine, entry, context, motor_context):
        return entry["anchor"]
    anchor, deps = engine._build_cross_track_fade_in_anchor(
        clip, universe, track=track, proj=proj, candidates=list(candidates))
    deps.add(key[0])
    from .timeline_incremental_checkpoint import copy_tree
    entry = dict(context=copy_tree(context), motor_context=motor_context,
                 anchor=copy_tree(anchor), deps=frozenset(deps))
    from .transition_anchor_geometry import attach_source_geometry
    attach_source_geometry(engine, clip, entry)
    updated = dict(state)
    updated[key] = entry
    setattr(engine, STATE, updated)
    setattr(engine, REVISION, getattr(engine, REVISION, 0) + 1)
    return entry["anchor"]


def capture_anchor_state(engine):
    """Job-owned immutable anchor values; revision is a writer token, not a hash."""
    return getattr(engine, REVISION, 0), getattr(engine, STATE, {})


def restore_anchor_state(engine, state, *, source_certificate=None):
    from .timeline_incremental_checkpoint import copy_tree
    from .transition_anchor_geometry import bind_owned_anchor_tokens, authorize_source_geometry
    setattr(engine, STATE, copy_tree(bind_owned_anchor_tokens(engine, state or {})))
    authorize_source_geometry(engine, getattr(engine, STATE), source_certificate=source_certificate)
    setattr(engine, REVISION, getattr(engine, REVISION, 0) + 1)


def clear_anchor_state(engine, clip_id=None):
    from .transition_anchor_geometry import PERMIT, PENDING
    setattr(engine, PERMIT, None)
    setattr(engine, PENDING, None)
    clip_id = None if clip_id is None else str(clip_id or "")
    state = getattr(engine, STATE, {})
    updated = {} if clip_id is None else {
        key: value for key, value in state.items()
        if key[0] != clip_id and clip_id not in value["deps"]}
    setattr(engine, STATE, updated)
    setattr(engine, REVISION, getattr(engine, REVISION, 0) + 1)
    # Derived candidates retain real Clip/Track objects and never enter a journal.
    getattr(engine, POLICY, {}).clear()
