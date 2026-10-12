"""Owned preparation: eliminate vetoed rows, preserve original hold exits.

The old table walk's only temporal write is active_local's hold latch. For a
fresh zero-origin owned range, each admitted clip is sampled monotonically:
its final admitted sample determines the exact final latch. Source/kernel
math is delegated to the existing CE, with no new numerical implementation.
All preparation checkpoints remain in their original order. Foreign callbacks
and helpers retain the original full preparation walk.
"""
import inspect
import math
from types import FunctionType

_METHODS = (
    '_indexed_active_clip_items', '_playback_index_for_tracks',
    '_build_playback_active_index', '_render_struct_for_tracks',
    '_render_track_state', '_track_state_signature', '_project_clip_layout_signature',
    '_clip_hold_mode', '_target_universes_for_clip', '_clip_index_scope_mask',
    '_track_filter_mask_for_universe', '_track_or_ancestor_muted',
    '_track_or_ancestor_soloed', '_clip_cache_id', '_clip_source_duration',
    '_clip_sequential_source_time', '_clip_run_mode_source_time',
    '_clip_auto_hold_cycle_time', '_clip_fade_gain', '_clip_transition_gain',
    '_clip_channel_active_mask', '_clip_playback_rate',
    '_clip_base_order_channel_mask', '_expand_motor_pair_mask_for_universe',
)


def _default_instance_methods(owner):
    return (not any(callable(item) and name.startswith(('_clip_', '_track_', '_playback_'))
                    for name, item in vars(owner).items())
            and not any(name in vars(owner) for name in _METHODS))


def _plain(owner, cls):
    return (type(owner) is cls and type(cls.__getattribute__) is type(object.__getattribute__)
            and _default_instance_methods(owner)
            and not any(hasattr(inspect.getattr_static(cls, name, None), '__get__')
                        for name in getattr(cls, '__dataclass_fields__', {})))


def _fresh_owned(engine, cls):
    return (_plain(engine, cls) and engine._timeline_bake_owned
            and engine._last_render_time is None and not engine._hold_boundary_latch)


def _ordinary_clip(clip, cls, ids):
    return (_plain(clip, cls) and type(clip.id) is str and bool(clip.id)
            and clip.id not in ids and math.isfinite(clip.start_time)
            and math.isfinite(clip.duration))


def eligible(engine, tracks):
    from src.core.models import Clip, Track
    from src.engine.playback_engine import PlaybackEngine
    if not _fresh_owned(engine, PlaybackEngine):
        return False
    ids = set()
    for track in tracks:
        if not _plain(track, Track):
            return False
        for clip in track.clips:
            if not _ordinary_clip(clip, Clip, ids):
                return False
            ids.add(clip.id)
    return True


def _last_sample(count, interval, end, stop):
    from qascade_ce import native_source_motion_clock as clock
    low, high = 0, count
    while low < high:
        mid = (low + high) // 2
        if clock.range_position(mid, interval, end) < stop:
            low = mid + 1
        else:
            high = mid
    return low - 1


def preserve_latches(engine, tracks, count, interval, end):
    from qascade_ce import native_source_motion_clock as clock
    from qascade_ce.prepared_timeline import table_render_cadence
    from src.engine import render_timing_math as timing
    structure = engine._render_struct_for_tracks(tracks)
    ordered, by_id = structure['ordered_tracks'], structure['track_by_id']
    index = engine._playback_index_for_tracks(tracks, ordered)
    for start, stop, item in index['active_events']:
        origin, order, track, clip, duration, mode, universes = item
        if mode == 'none' or engine._track_or_ancestor_muted(track, by_id):
            continue
        if structure['any_solo'] and not engine._track_or_ancestor_soloed(track, by_id):
            continue
        # First sampled position at or beyond the original active-window end.
        last = _last_sample(count, interval, end, stop)
        if last < 0:
            continue
        position = clock.range_position(last, interval, end)
        if timing.active_admitted(position, start, stop):
            timing.active_local(engine, clip,
                (position, origin, duration, table_render_cadence(engine, position), mode, order))


def finish(engine, tracks, frame_count, *, interval, end, prepared, checkpoint, callback):
    preserve_latches(engine, tracks, frame_count, interval, end)
    for index in range(frame_count):
        checkpoint(prepared[0], index, callback)
    return prepared
