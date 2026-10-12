"""Native per-channel math; original public containers own consumption state.

CE-free operation and a native error preserve the existing NumPy semantics.
Normal default-native verification requires zero recorded fallback/error delta.
"""
import numpy as np
from ..core.native_mask_math import (
    domain, _fallback, mask_math, mask_count, mask_and, mask_or, mask_without,
    merge_consumed, filtered_active,
    metadata_group_ids, is_default_restoration_rule,
)


def pump_level(source, indices):
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.pump(source, indices)
        except Exception:
            _fallback()
    values = np.asarray(source, np.uint8)
    return int(np.max(values[indices])) if indices and values.size > max(indices) else 0


def color_source(source, indices):
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.color(source, indices)
        except Exception:
            _fallback()
    if source is None:
        return np.asarray([255, 255, 255], np.uint8)
    values = np.asarray(source, np.uint8)
    colors = [255 if index < 0 else int(values[index]) for index in indices[:3]]
    if any(colors):
        return np.asarray(colors, np.uint8)
    white = 255 if indices[3] < 0 else int(values[indices[3]])
    return np.asarray([white, white, white], np.uint8)


def ownership_math(candidates, chains, restorable):
    module = domain()
    if module is None or module.NATIVE is None:
        return None
    if not is_default_restoration_rule(restorable):
        module.note_unsupported_callback()
        return None
    groups, _unique = metadata_group_ids(chains)
    identities = {}
    identity_ids = np.asarray([identities.setdefault(id(row), len(identities))
                               for row in candidates], np.int32)
    payload = (np.stack([row[1] for row in candidates]), np.stack([row[3] for row in candidates]),
               np.stack([row[0] for row in candidates]), groups, identity_ids, None)
    try:
        return module.NATIVE.ownership_compact(payload)
    except Exception:
        _fallback()
        return None


def apply_hold_consumption(engine, maps, registry):
    from .playback_engine import _release_hold_consumed
    from .hold_metadata_index import hold_metadata_queries
    if not _hold_inputs_ready(engine, maps):
        return False
    queries = hold_metadata_queries(maps, registry)
    if queries is None:
        return False
    needed = {universe for universe, _key, has_peer, index in queries
              if has_peer and index is not None}
    prepared = _group_math(engine, maps, skip=maps[0].keys() - needed)
    if prepared is None:
        return False
    for universe, key, has_peer, index in queries:
        if not has_peer:
            _release_hold_consumed(engine._hold_consumed_masks, key, universe)
            continue
        if index is not None:
            _update_hold(engine, key, universe, prepared[universe][1][index])
    return True


def _hold_inputs_ready(engine, maps):
    if not _hold_group_shapes_valid(maps):
        return False
    if not any(maps[0].values()):
        return True
    module = domain()
    if module is None or module.NATIVE is None:
        return False
    if not is_default_restoration_rule(engine._is_restorable_takeover):
        module.note_unsupported_callback()
        return False
    return True


def _hold_group_shapes_valid(maps):
    candidates, metas = maps
    for universe, rows in candidates.items():
        if not rows:
            continue
        chains = [tuple(meta[1] or ()) for meta in metas.get(universe, [])]
        if len(chains) != len(rows):
            return False
    return True


def _update_hold(engine, key, universe, lost):
    lost_count = mask_count(lost)
    if not lost_count:
        return
    table = engine._hold_consumed_masks.setdefault(key, {})
    previous = table.get(int(universe))
    merged, added = (lost.copy(), lost_count) if previous is None else merge_consumed(previous, lost)
    if previous is not None:
        np.copyto(previous, merged)
        merged = previous
    table[int(universe)] = merged
    if added:
        engine.unlock_timeline_render_cache()


def _group_math(engine, maps, skip=()):
    candidates, metas = maps
    result = {}
    for universe, rows in candidates.items():
        if universe in skip or not rows:
            continue
        chains = [tuple(meta[1] or ()) for meta in metas.get(universe, [])]
        if len(chains) != len(rows):
            return None
        computed = ownership_math(rows, chains, engine._is_restorable_takeover)
        if computed is None:
            return None
        result[universe] = computed
    return result


def apply_takeover_consumption(engine, maps, skip):
    from .native_takeover_batch import selected_indices
    import sys
    candidates, metas = maps
    prepared = _group_math(engine, maps, skip)
    if prepared is None:
        return False
    for universe, results in prepared.items():
        selected = selected_indices(engine, sys.modules[__name__], universe, results, metas[universe])
        if selected is not None:
            for raw_index in selected:
                index = int(raw_index)
                clip_id, _chain = metas[universe][index]
                engine._mark_takeover_consumed(clip_id, int(universe), results[2][index])
            continue
        for index, (clip_id, _chain) in enumerate(metas[universe]):
            lost = results[2][index]
            if mask_count(lost):
                engine._mark_takeover_consumed(clip_id, int(universe), lost)
    return True


def legacy_scores(scores, active, rules, tie_boost):
    ties, boosted, boost = tie_boost
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.legacy((scores, active, rules, ties, boosted, boost))
        except Exception:
            _fallback()
    values = np.asarray(scores, np.float64) + np.asarray(ties, np.float64)[:, None]
    for index, flag in enumerate(boosted):
        if flag:
            values[index] += boost
    return values, np.asarray(active, bool) & np.asarray(rules, bool)


def apply_legacy_consumption(engine, context, dump, metadata, state):
    """Original static K2 consumption; hold loss differs from runtime peers.

    Keep the real TimelineCtx render output and its slot/engine state writes.
    This adapter handles only dumped candidate math after that native render.
    """
    module = domain()
    if module is None or module.NATIVE is None:
        return False
    index, consumed, skipped = state
    first = int(context.task_off[index])
    last = int(context.task_off[index + 1]) if index + 1 < context.task_off.shape[0] else first
    last = max(first, last)
    ties = context._tie[first:last]
    if ties.shape[0] != len(metadata):
        return True
    universes, slots = context._uni[first:last], context._slot[first:last]
    rules = [_legacy_rule(context, slot) for slot in slots]
    flags = [bool(row[2]) for row in metadata]
    from .native_frame_clock import consumption_boost
    values, masks = legacy_scores(dump[0], dump[1], rules,
                                  (ties, flags, consumption_boost(len(metadata))))
    by_universe = {}
    for offset, universe in enumerate(universes):
        by_universe.setdefault(int(universe), []).append(offset)
    groups = _legacy_group_math(engine, dump[2], metadata, (values, masks),
                                (by_universe, skipped))
    if groups is None:
        return False
    for group in groups:
        _legacy_store_group(engine, context, metadata, group, consumed)
    return True


def _legacy_rule(context, slot):
    rule = context._slot_rule.get(int(slot))
    return np.ones(512, bool) if rule is None else rule


def _legacy_group_math(engine, output, metadata, arrays, grouping):
    values, masks = arrays
    by_universe, skipped = grouping
    groups = []
    for universe, indices in by_universe.items():
        if universe in skipped or len(indices) < 2:
            continue
        rows = [(output[index], masks[index], None, values[index]) for index in indices]
        chains = [tuple(metadata[index][1] or ()) for index in indices]
        result = ownership_math(rows, chains, engine._is_restorable_takeover)
        if result is None:
            return None
        groups.append((universe, indices, result, masks[indices]))
    return groups


def _legacy_store_group(engine, context, metadata, group, consumed):
    universe, indices, result, active = group
    effective, _hold_peers, takeover, _winner = result
    if not mask_count(effective):
        return
    _legacy_hold_group(engine, context, metadata, (universe, indices, effective, active), consumed)
    for local, index in enumerate(indices):
        _legacy_store_takeover(engine, context, (metadata[index][0], universe), takeover[local], consumed)


def _legacy_hold_group(engine, context, metadata, group, consumed):
    universe, indices, effective, active = group
    for local, index in enumerate(indices):
        if metadata[index][3]:
            lost = mask_without(active[local], effective[local])
            _legacy_store_hold(engine, context, (metadata[index][0], universe), lost, consumed)


def _legacy_store_hold(engine, context, identity, lost, consumed):
    if not mask_count(lost):
        return
    key, universe = identity
    table = getattr(engine, "_hold_consumed_masks", None)
    prior = None if table is None else table.get(key, {}).get(universe)
    merged = lost.copy() if prior is None else mask_or(lost, prior)
    if table is not None:
        table.setdefault(key, {})[universe] = merged.copy()
    slot = context.slot_of(key, universe)
    if slot is not None and mask_count(merged):
        previous = consumed.get(slot)
        pushed = merged if previous is None else mask_or(merged, previous)
        consumed[slot] = pushed
        context.set_consumed(slot, pushed)


def _legacy_store_takeover(engine, context, identity, lost, consumed):
    if not mask_count(lost):
        return
    key, universe = identity
    slot = context.slot_of(key, universe)
    if slot is None:
        return
    previous = consumed.get(slot)
    merged = lost if previous is None else mask_or(lost, previous)
    consumed[slot] = merged
    context.set_consumed(slot, merged)
    table = getattr(engine, "_takeover_consumed_masks", None)
    if table is not None:
        table.setdefault(key, {})[universe] = merged.copy()
