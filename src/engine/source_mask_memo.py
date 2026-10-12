"""Track ownership sampling metadata independently of nearest DMX-row memo."""
from src.core.recorded_channel_masks import recorded_mask_token
from .source_sampling_math import floor_index


def _sampling_index(engine, clip, universe, source_t, hold):
    if hold or recorded_mask_token(clip, universe, source_t) is not None:
        return None
    if engine._clip_sequential_group_timing(clip)[0]:
        return None
    prepared = engine._clip_mask_frames_cached(clip, universe)
    if prepared is None:
        return None
    return floor_index(prepared[0], source_t)


def matches(engine, context, memo):
    key, clip, universe, source_t, hold = context
    index = _sampling_index(engine, clip, universe, source_t, hold)
    if index is None:
        return True
    entry = getattr(engine, "_dirty_render_source_memo", {}).get(key)
    return entry == (memo["sig"], index)


def remember(engine, context, memo):
    key, clip, universe, source_t, hold = context
    index = _sampling_index(engine, clip, universe, source_t, hold)
    table = getattr(engine, "_dirty_render_source_memo", None)
    if table is None:
        table = {}
        engine._dirty_render_source_memo = table
    table[key] = (memo["sig"], index)
    if len(table) > 8192:
        table.pop(next(iter(table)))
