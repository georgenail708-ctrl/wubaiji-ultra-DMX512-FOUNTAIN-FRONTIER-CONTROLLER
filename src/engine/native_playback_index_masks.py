"""Original cold membership/cache owners; only mask math uses shared CE."""
import sys

import numpy as np

from src.core.native_mask_math import mask_count, mask_math, mask_or
from .native_playback_filter_cache import MISSING, lookup as filter_lookup, publish as filter_publish


def source():
    return sys.modules["src.engine.playback_engine"]


def _active_scope(clip, universe, mask):
    active_map = getattr(clip, "active_channels_by_universe", {}) or {}
    channels = active_map.get(universe) or active_map.get(str(universe))
    if channels:
        _paint_channels(mask, channels)
        return bool(mask_count(mask))
    return False


def scope_mask(engine, clip, universe):
    universe = int(universe)
    mask = engine._get_array_bool_512()
    try:
        if _active_scope(clip, universe, mask):
            return mask
    except Exception:
        source().log.exception("active_channels_by_universe read failed for clip %s",
                               getattr(clip, "id", None))
    try:
        base = engine._clip_base_order_channel_mask(clip, universe)
        if base is not None and mask_count(base):
            return np.asarray(base, dtype=bool).copy()
    except Exception:
        source().log.exception("base_order channel mask lookup failed for clip %s",
                               getattr(clip, "id", None))
    mask[:] = True
    return mask


def _paint_channels(mask, channels):
    for channel in channels:
        index = int(channel) - 1
        if 0 <= index < 512:
            mask[index] = True


def accumulate_hold(engine, track, clip, targets, scopes):
    for universe in targets:
        active = engine._clip_index_scope_mask(clip, int(universe))
        rule = engine._track_filter_mask_for_universe(track, int(universe))
        if rule is not None:
            active, count = mask_math(1, active, np.asarray(rule, dtype=bool))
        else:
            count = mask_count(active)
        if not count:
            continue
        scope = scopes.setdefault(int(universe), np.zeros(512, dtype=bool))
        np.copyto(scope, mask_or(scope, active))


def _paint_members(engine, universe, fixture_ids, indices, mask):
    definitions, fixtures = indices
    for fixture_id in fixture_ids:
        fixture = fixtures.get(str(fixture_id))
        if fixture is None:
            continue
        try:
            if int(getattr(fixture, "universe", 1) or 1) != int(universe):
                continue
        except Exception:
            continue
        start, end = engine._fixture_channel_span(fixture, definitions)
        if 0 <= start < end <= 512:
            mask[start:end] = True


def base_order_mask(engine, clip, universe):
    raw_ids = getattr(clip, "base_order_fixture_ids", []) or []
    fixture_ids = engine._unique_fixture_id_list(raw_ids)
    if not fixture_ids:
        return None
    key = (engine._clip_cache_id(clip), int(universe), tuple(fixture_ids))
    cached = engine._clip_base_order_mask_cache.get(key)
    if cached is not None:
        return None if cached is source()._EMPTY_MASK_SENTINEL else cached
    project = source()._project_of(engine)
    if project is None:
        return None
    indices = engine._fixture_defs_index(project), engine._fixtures_by_id_index(project)
    if not indices[1]:
        return None
    mask = np.zeros(512, dtype=bool)
    _paint_members(engine, universe, fixture_ids, indices, mask)
    result = mask if mask_count(mask) else None
    engine._clip_base_order_mask_cache[key] = source()._EMPTY_MASK_SENTINEL if result is None else result
    return result


def _expanded_copy(mask):
    expanded = np.asarray(mask, dtype=bool)
    if expanded.shape == (512,):
        return expanded.copy()
    fixed = np.zeros(512, dtype=bool)
    size = min(expanded.size, 512)
    if size > 0:
        fixed[:size] = expanded.reshape(-1)[:size]
    return fixed


def _channel_types(fixture, definition):
    by_type = {}
    start = int(getattr(fixture, "start_channel", 1) or 1) - 1
    for offset, channel_type in enumerate(getattr(definition, "channels", []) or []):
        index = start + int(offset)
        if 0 <= index < 512:
            by_type.setdefault(str(channel_type), []).append(index)
    return by_type


def _fine_pairs(definition):
    pairs = {}
    for channel_type in getattr(definition, "channels", []) or []:
        name = str(channel_type)
        if name.endswith("_fine") and name[:-5]:
            pairs.setdefault(name[:-5], []).append(name)
    return pairs


def _activate_pairs(expanded, by_type, pairs):
    for coarse, fine in pairs.items():
        pair = list(by_type.get(coarse, []) or [])
        for name in fine:
            pair.extend(list(by_type.get(name, []) or []))
        if pair and mask_count(expanded[pair]):
            expanded[pair] = True


def _paint_motor_pairs(universe, project, expanded):
    definitions = {str(getattr(definition, "id", "")): definition
                   for definition in getattr(project, "fixture_defs", []) or []
                   if getattr(definition, "id", None)}
    for fixture in getattr(project, "fixtures", []) or []:
        if int(getattr(fixture, "universe", 1) or 1) != int(universe):
            continue
        definition = definitions.get(str(getattr(fixture, "def_id", "")))
        by_type = _channel_types(fixture, definition)
        _activate_pairs(expanded, by_type, _fine_pairs(definition))


def expand_motor_mask(engine, universe, mask, project=None):
    expanded = _expanded_copy(mask)
    if not mask_count(expanded):
        return expanded
    if project is None:
        project = source()._project_of(engine)
    if project is None:
        return expanded
    try:
        _paint_motor_pairs(universe, project, expanded)
    except Exception:
        pass
    return expanded


def _filter_channels(mask, channels):
    for channel in channels:
        try:
            index = int(channel) - 1
        except Exception:
            continue
        if 0 <= index < 512:
            mask[index] = True


def track_filter_mask(engine, track, universe, project=None):
    rule = engine._track_filter_rule_for_universe(track, universe)
    if rule is None:
        return None
    try:
        if rule.is_empty() or rule.allows_all():
            return None
    except Exception:
        pass
    cached, token = filter_lookup(engine, track, universe, rule, project)
    if cached is not MISSING:
        return cached
    mask = np.zeros(512, dtype=bool)
    _filter_channels(mask, getattr(rule, "channels", []) or [])
    if not mask_count(mask):
        return None
    expanded = engine._expand_motor_pair_mask_for_universe(universe, mask, proj=project)
    return filter_publish(token, expanded)
