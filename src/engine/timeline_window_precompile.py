"""Advisory live precompile owns its Clip metadata, borrowing original frames.

Reuse stdlib copy.copy and the existing original precompile/cache implementation.
Original source Project/Track and every authored coordinate remain authoritative.
No project clone, new render math, mutable fingerprint memo or source write.
"""
from __future__ import annotations

from copy import copy
import copyreg
import hashlib
import sys
import threading
from types import FunctionType

_EXPECTED = {'precompile_clip': 'bb3b81e76fa0c2e33af0711e4ba39c769b8551822af48b1d8ba2f9157fb922ef', '_clip_static_signature': '4ff3a463d1994ee9c5cfd3da416a10dd4dc4e536c295773363df0673c6c0d7cc', '_clip_cache_id': 'e60654f45158acaa79ec2ff6b951a2e1ae7e48f194221cb437b5f11a1ef52633', '_unique_fixture_id_list': '425939173d2c8d478aa986a711edf717b7f4ac6c0624199f8bd24b6eceeef989', '_evict_oldest': '4d1ace21e71fa5652ba99d701d2a39ef9022e9b235e8f2ad6a00d129770db9bc'}
_DEFAULTS = {"precompile_clip": (None, None), "_clip_static_signature": None,
             "_clip_cache_id": None, "_unique_fixture_id_list": None,
             "_evict_oldest": None}
_QUALIFIED = None


def _function(owner, name):
    descriptor = vars(owner).get(name)
    return descriptor.__func__ if type(descriptor) is staticmethod else descriptor


def _initial_source(owner):
    from qascade_ce.native_curve_scalar_guard import _signature
    from .transition_anchor_causality import _defaults_current
    namespace, result = vars(sys.modules[owner.__module__]), {}
    for name, expected in _EXPECTED.items():
        function = _function(owner, name)
        if type(function) is not FunctionType or function.__globals__ is not namespace:
            return None
        if not _defaults_current(function, _DEFAULTS[name]) or function.__kwdefaults__ is not None:
            return None
        if hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() != expected:
            return None
        result[name] = function, function.__code__, function.__defaults__, function.__kwdefaults__
    return result


def _source_current(owner):
    for name, previous in _QUALIFIED.items():
        function = _function(owner, name)
        if (function is not previous[0] or function.__code__ is not previous[1]
                or function.__defaults__ is not previous[2] or function.__kwdefaults__ is not previous[3]):
            return False
    return True


def _plain_sequence(value, kinds):
    return type(value) in (list, tuple) and all(type(item) in kinds for item in value)


def _plain_positions(value):
    return (type(value) is dict and all(type(key) is str and type(item) in (list, tuple)
            and len(item) >= 2 and type(item[0]) in (float, int, bool)
            and type(item[1]) in (float, int, bool) for key, item in value.items()))


def _plain_active(value):
    return (type(value) is dict and all(type(key) in (int, str)
            and _plain_sequence(item, (int, bool)) for key, item in value.items()))


def _plain_clip_fields(fields):
    return all((type(fields.get("id")) is str, bool(fields.get("id")),
                _plain_sequence(fields.get("clip_universes"), (int, bool)),
                _plain_sequence(fields.get("base_order_fixture_ids"), (str,)),
                _plain_positions(fields.get("base_fixture_positions")),
                _plain_active(fields.get("active_channels_by_universe"))))


def _plain_clip_copy_type(owner):
    return all(("__copy__" not in vars(owner), "__setstate__" not in vars(owner),
                owner.__setattr__ is object.__setattr__, owner.__new__ is object.__new__,
                owner.__getstate__ is object.__getstate__, owner.__reduce__ is object.__reduce__,
                owner.__reduce_ex__ is object.__reduce_ex__, owner not in copyreg.dispatch_table))


def _clip_source_fields_current(owner):
    return all(name not in vars(owner) for name in (
        "id", "frames", "clip_universes", "base_order_fixture_ids", "base_fixture_positions",
        "active_channels_by_universe"))


def _plain_clip(clip):
    from src.core.models import Clip
    from .transition_anchor_causality import _primitive_filter_parameters
    if type(clip) is not Clip or not _primitive_filter_parameters(clip):
        return False
    fields = vars(clip)
    return (_plain_clip_fields(fields) and type(fields.get("frames")) is list
            and _plain_clip_copy_type(Clip) and _clip_source_fields_current(Clip))


def _scope_types(engine, track, project):
    from src.core.models import Project, Track
    from .playback_engine import PlaybackEngine
    return (type(engine) is PlaybackEngine and type(project) is Project
            and (track is None or type(track) is Track))


def _scope_metadata(engine, track):
    if vars(engine).get("_timeline_bake_owned", False) is not False:
        return False
    if any(name in vars(engine) for name in _EXPECTED):
        return False
    if track is not None and not _plain_sequence(vars(track).get("universes"), (int, bool)):
        return False
    return all(type(vars(engine).get(name)) is dict for name in
               ("_clip_static_signature_cache", "_clip_compiled_fast", "_clip_frame_universes_cache"))


def _ordinary_scope(engine, clip, track, project):
    from .playback_engine import PlaybackEngine
    from .transition_anchor_geometry import spatial_scope
    global _QUALIFIED
    if not _scope_types(engine, track, project) or not _scope_metadata(engine, track):
        return False
    if not spatial_scope(engine) or not _plain_clip(clip):
        return False
    if _QUALIFIED is None:
        _QUALIFIED = _initial_source(PlaybackEngine)
    return _QUALIFIED is not None and _source_current(PlaybackEngine)


def _retire_private_fast_entries(engine, private, track):
    fields = vars(private)
    universes = set(fields["clip_universes"])
    cached = engine._clip_frame_universes_cache.get(fields["id"])
    if cached is not None:
        universes.update(cached[1])
    track_universes = vars(track).get("universes") if track is not None else None
    universes.update(track_universes or (1,))
    for universe in universes:
        engine._clip_compiled_fast.pop((id(private), universe, id(track) if track is not None else None), None)


def precompile_owned_window_clip(engine, clip, *, track=None, proj=None):
    """Advisory live callers detach metadata; owned bake/foreign paths stay original."""
    if not _ordinary_scope(engine, clip, track, proj):
        return engine.precompile_clip(clip, track=track, proj=proj)
    # This memo belongs to the source Clip. Private default geometry must never
    # replace it with a filled private-context signature shared under clip.id.
    engine._clip_static_signature(clip)
    private = copy(clip)
    try:
        return engine.precompile_clip(private, track=track, proj=proj)
    finally:
        # The original semantic quick cache remains warm. Retire only temporary
        # id(private) strong refs so repeated windows do not grow the fast table.
        _retire_private_fast_entries(engine, private, track)


def precompile_background_project_clip(engine, clip, *, track=None, proj=None):
    """Only the original all-project daemon is advisory; public capture stays original."""
    if vars(engine).get("_precompile_thread") is threading.current_thread():
        return precompile_owned_window_clip(engine, clip, track=track, proj=proj)
    return engine.precompile_clip(clip, track=track, proj=proj)
