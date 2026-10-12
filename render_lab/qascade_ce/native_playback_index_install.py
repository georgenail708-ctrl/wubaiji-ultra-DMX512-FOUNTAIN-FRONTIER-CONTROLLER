"""Six explicit PE hooks; existing defaults/cache containers remain authoritative."""
import inspect
from contextvars import ContextVar

from src.engine import native_playback_index as host
from src.engine import native_playback_index_math as math
from src.engine import native_playback_index_masks as masks

_COMPAT = ContextVar("qascade_cold_index_compat", default=False)
_ORIGINALS, _SAFE, _WRAPPERS = {}, {}, {}
_DEPENDENCIES = {
    "_build_playback_active_index": ("_clip_index_scope_mask", "_track_filter_mask_for_universe"),
    "_playback_index_for_tracks": ("_build_playback_active_index",),
    "_clip_index_scope_mask": ("_clip_base_order_channel_mask",),
    "_track_filter_mask_for_universe": ("_expand_motor_pair_mask_for_universe",),
}


def _original(name, owner, arguments):
    token = _COMPAT.set(True)
    try:
        return _ORIGINALS[name](owner, *arguments)
    finally:
        _COMPAT.reset(token)


def _safe_warm_fields(owner):
    state = object.__getattribute__(owner, "__dict__")
    names = ("_playback_index_check_time", "_playback_index_check_interval")
    for name in names:
        if name not in state:
            return False
        declared = inspect.getattr_static(type(owner), name, None)
        if declared is not None and hasattr(declared, "__get__"):
            return False
    return type(type(owner).__getattribute__) is type(object.__getattribute__)


def _entry_default(owner, name):
    state = object.__getattribute__(owner, "__dict__")
    return (name not in state and inspect.getattr_static(type(owner), name) is _WRAPPERS[name]
            and _SAFE[name])


def _dependencies_default(owner, name):
    names = _DEPENDENCIES.get(name, ())
    return (type(type(owner).__getattribute__) is type(object.__getattribute__)
            and all(_entry_default(owner, dependency) for dependency in names))


def _dispatch(name, owner, arguments, implementation):
    module = math.timing()
    if _COMPAT.get() or module is None or not module.available():
        return _ORIGINALS[name](owner, *arguments)
    if not _SAFE[name] or not _dependencies_default(owner, name):
        math.note_unsupported()
        return _original(name, owner, arguments)
    return implementation(owner, *arguments)


def _build(owner, tracks, ordered_tracks):
    return _dispatch("_build_playback_active_index", owner, (tracks, ordered_tracks), host.build_index)


def _index(owner, tracks, ordered_tracks):
    if not _COMPAT.get() and not _safe_warm_fields(owner):
        math.note_unsupported()
        return _original("_playback_index_for_tracks", owner, (tracks, ordered_tracks))
    return _dispatch("_playback_index_for_tracks", owner, (tracks, ordered_tracks), host.index_for_tracks)


def _scope(owner, clip, universe):
    return _dispatch("_clip_index_scope_mask", owner, (clip, universe), masks.scope_mask)


def _base(owner, clip, universe):
    return _dispatch("_clip_base_order_channel_mask", owner, (clip, universe), masks.base_order_mask)


def _motor(owner, universe, mask, proj=None):
    return _dispatch("_expand_motor_pair_mask_for_universe", owner,
                     (universe, mask, proj), masks.expand_motor_mask)


def _filter(owner, track, universe, proj=None):
    return _dispatch("_track_filter_mask_for_universe", owner,
                     (track, universe, proj), masks.track_filter_mask)


METHODS = (("_build_playback_active_index", _build),
           ("_playback_index_for_tracks", _index),
           ("_clip_index_scope_mask", _scope),
           ("_clip_base_order_channel_mask", _base),
           ("_expand_motor_pair_mask_for_universe", _motor),
           ("_track_filter_mask_for_universe", _filter))


def _source_default(function, name):
    code = getattr(function, "__code__", None)
    return (code is not None and code.co_name == name
            and code.co_filename.replace("\\", "/").endswith("src/engine/playback_engine.py"))


def install(engine):
    if _ORIGINALS:
        return True
    module = math.timing()
    if module is None or not module.available():
        raise RuntimeError("Cold playback index requires the mounted render timing domain")
    from src.engine.playback_engine import PlaybackEngine
    from src.engine.native_playback_filter_cache import configure
    from .native_playback_filter_guard import FilterGuard
    import sys
    configure(FilterGuard(PlaybackEngine, sys.modules[__name__]))
    for name, wrapper in METHODS:
        original = getattr(PlaybackEngine, name)
        _ORIGINALS[name] = original
        _SAFE[name] = _source_default(original, name)
        _WRAPPERS[name] = wrapper
        setattr(PlaybackEngine, name, wrapper)
    return True
