"""NM full-frame mutation phases, with its original lock and ownership rules.

No sender, policy, cache or queue is created here. Pending drain is called
with NM's state lock held; every other mutation acquires that same lock.
The original bit comparator is supplied by NM to avoid duplicate algorithms.
"""
from __future__ import annotations

import numpy as np

from ..core.constants import BASE_LAYERS, CLIP_LAYERS, SHAPE_LAYERS
from ..core.project_frame_origin import pending_project_frame, project_frame_mutation_allowed


def normalize_frame_array(data, dtype):
    """Keep the original uint8/bool cast, flatten and zero-padding contract."""
    array = np.asarray(data, dtype=dtype)
    if array.shape == (512,):
        return array
    buffer = np.zeros(512, dtype=dtype)
    size = min(array.size, 512)
    buffer[:size] = array.flatten()[:size]
    return buffer


def _same_frame(nm, layer_name, universe, frame, *, frame_equal):
    previous = nm._layers[layer_name].get(universe)
    return (previous is not None and int(universe) in nm._universe_snapshot
            and frame_equal(previous[0], previous[1], frame[0], frame[1]))


def _touch_unchanged_frame(nm, layer_name, universe, active, *, touch_ltp):
    if layer_name in CLIP_LAYERS:
        return False
    ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
    if touch_ltp and ltp_layer:
        nm._touch_editor_ltp(int(universe), active, layer_name)
        nm._mark_dirty_universe(universe)
        return True
    return False


def _update_frame_locked(nm, layer_name, universe, frame, *, touch_ltp, frame_equal):
    if not project_frame_mutation_allowed(nm, layer_name):
        return False
    data, active = frame
    if _same_frame(nm, layer_name, universe, frame, frame_equal=frame_equal):
        return _touch_unchanged_frame(nm, layer_name, universe, active, touch_ltp=touch_ltp)
    nm._layers[layer_name][universe] = (data.copy(), active.copy())
    ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
    if touch_ltp and ltp_layer:
        nm._touch_editor_ltp(int(universe), active, layer_name)
    nm._mark_dirty_universe(universe)
    return True


def update_layer_frame(nm, layer_name, universe, data, *, active, touch_ltp, frame_equal):
    """Normalize before taking the state lock, exactly as NM's old entry did."""
    if layer_name not in nm._layers:
        return None
    normalized_data = normalize_frame_array(data, np.uint8)
    normalized_active = (np.ones(512, dtype=bool) if active is None
                         else normalize_frame_array(active, bool))
    with nm._state_lock:
        return _update_frame_locked(nm, layer_name, universe,
                                    (normalized_data, normalized_active),
                                    touch_ltp=touch_ltp, frame_equal=frame_equal)


def _clear_shape_universe(nm, layer_name, universe):
    if layer_name in nm._shape_runmin:
        nm._shape_runmin[layer_name].pop(universe, None)
        nm._shape_runmax[layer_name].pop(universe, None)


def _clear_pending_frame(nm, layer_name, universe):
    previous = nm._layers[layer_name].pop(universe, None)
    if previous is None:
        return False
    _clear_shape_universe(nm, layer_name, universe)
    nm._clear_layer_ltp_locked(layer_name, [universe])
    nm._dirty_universes.add(universe)
    return True


def _commit_pending_frame(nm, layer_name, universe, entry, *, frame_equal):
    if entry is None:
        return _clear_pending_frame(nm, layer_name, universe)
    layer = nm._layers[layer_name]
    data, active = entry
    previous = layer.get(universe)
    if previous is not None and frame_equal(previous[0], previous[1], data, active):
        return False
    layer[universe] = (data, active)
    nm._dirty_universes.add(universe)
    return True


def drain_layer_frames_locked(nm, *, frame_equal):
    """Drain original latest slots; caller owns state lock, then pending lock."""
    with nm._pending_layer_updates_lock:
        pending = nm._pending_layer_updates
        nm._pending_layer_updates = {}
    if not pending:
        return set()
    changed = set()
    for layer_name, per_universe in pending.items():
        if layer_name not in nm._layers:
            continue
        for universe, entry in per_universe.items():
            accepted, entry = pending_project_frame(nm, entry)
            if not accepted:
                continue
            universe_i = int(universe)
            if _commit_pending_frame(nm, layer_name, universe_i, entry, frame_equal=frame_equal):
                changed.add(universe_i)
    return changed


def _clear_one_layer(nm, layer_name, universe):
    universe_i = int(universe)
    previous = nm._layers[layer_name].pop(universe_i, None)
    # Public clear removes shape extrema even without an existing frame.
    _clear_shape_universe(nm, layer_name, universe_i)
    if previous is not None:
        nm._clear_layer_ltp_locked(layer_name, [universe_i])
        nm._mark_dirty_universe(universe_i)


def _clear_whole_layer(nm, layer_name):
    old_entries = {int(u): entry for u, entry in list(nm._layers[layer_name].items())}
    known_universes = set(old_entries.keys())
    nm._layers[layer_name].clear()
    if layer_name in nm._shape_runmin:
        nm._shape_runmin[layer_name].clear()
        nm._shape_runmax[layer_name].clear()
    nm._clear_layer_ltp_locked(layer_name, known_universes)
    for universe in known_universes:
        nm._mark_dirty_universe(int(universe))


def clear_layer_frame(nm, layer_name, universe=None):
    """Reject stale origin before touching either pending slots or layer state."""
    with nm._state_lock:
        if not project_frame_mutation_allowed(nm, layer_name):
            return
        nm._drop_pending_layer_updates(layer_name, universe)
        if layer_name not in nm._layers:
            return
        if universe is not None:
            _clear_one_layer(nm, layer_name, universe)
        else:
            _clear_whole_layer(nm, layer_name)
