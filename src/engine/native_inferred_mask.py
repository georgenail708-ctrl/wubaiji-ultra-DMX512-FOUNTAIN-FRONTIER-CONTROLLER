"""Original source-mask metadata order with one native inferred channel pass.

The public source body remains the independent original reference and fallback.
No source, raw or base mask is cached here; the existing frame preparation owns
its original strong references, timestamp floor and invalidation lifecycle.
"""
import numpy as np

from ..core.native_mask_math import domain, mask_and, mask_count
from .source_sampling_math import floor_index


def normalized_row(value, dtype):
    row = np.asarray(value, dtype=dtype)
    if row.shape == (512,):
        return row
    result = np.zeros(512, dtype=dtype)
    count = min(row.size, 512)
    if count:
        result[:count] = row.reshape(-1)[:count]
    return result


def inferred_neighbors(request, prepared):
    timestamps, data = prepared
    index = floor_index(timestamps, request["source_t"])
    supplied = request["current_raw"]
    current = data[index] if supplied is None else normalized_row(supplied, np.uint8)
    previous = data[index - 1] if index > 0 else None
    following = data[index + 1] if index + 1 < timestamps.shape[0] else None
    return current, previous, following


def current_mask(engine, request):
    base = normalized_row(request["base_active"], bool)
    if not mask_count(base):
        return base
    from ..core.recorded_channel_masks import recorded_mask_at
    clip, universe = request["clip"], request["universe"]
    recorded = recorded_mask_at(clip, universe, request["source_t"])
    if recorded is not None:
        return mask_and(base, recorded)
    if request["hold_extension"]:
        return base
    prepared = engine._clip_mask_frames_cached(clip, universe)
    if prepared is None:
        return base
    neighbors = inferred_neighbors(request, prepared)
    return domain().NATIVE.inferred(base, neighbors)
