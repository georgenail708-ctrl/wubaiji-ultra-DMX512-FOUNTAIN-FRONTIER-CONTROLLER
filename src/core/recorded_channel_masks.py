"""Source-time ownership changes for recorded frames, including authored zero.

The union mask still describes the clip's scope. These optional packed 512-bit
samples retain the actual writer mask at each change, including explicit release.
Legacy clips keep the existing inference. Reuse NumPy/base64/bisect, no new clock.
"""
from __future__ import annotations

import base64
from bisect import bisect_right
from functools import lru_cache
import math

import numpy as np

_EMPTY_MASK = np.zeros(512, dtype=bool)
_EMPTY_MASK.flags.writeable = False


def record_mask(samples, universe, timestamp, mask):
    if mask is None:
        return
    raw = np.asarray(mask, dtype=bool).reshape(-1)
    fixed = np.zeros(512, dtype=bool)
    fixed[:min(512, raw.size)] = raw[:512]
    packed = base64.b64encode(np.packbits(fixed).tobytes()).decode("ascii")
    events = samples.setdefault(int(universe), [])
    stamp = max(0., float(timestamp))
    if events and events[-1][0] == stamp:
        events.pop()
    if not events or events[-1][1] != packed:
        events.append([stamp, packed])


def normalized_masks(samples, offset=0.):
    """Validate optional saved metadata and detach it from mutable record buffers."""
    out = {}
    if not isinstance(samples, dict):
        return out
    for universe, events in samples.items():
        try:
            universe = int(universe)
        except (ValueError, TypeError):
            continue
        valid = _valid_events(events, float(offset))
        if valid:
            out[universe] = valid
    return out


def masks_from_dict(data):
    return normalized_masks(data.get("recorded_channel_masks", {}))


def _valid_events(events, offset):
    valid = []
    for event in events if isinstance(events, (list, tuple)) else ():
        try:
            stamp, packed = float(event[0]), str(event[1])
            data = base64.b64decode(packed, validate=True)
            if math.isfinite(stamp) and len(data) == 64:
                valid.append([max(0., stamp - offset), packed])
        except (ValueError, TypeError, IndexError):
            continue
    return sorted(valid, key=lambda event: event[0])


def shift_clip_masks(clip, offset):
    clip.recorded_channel_masks = normalized_masks(
        getattr(clip, "recorded_channel_masks", {}), offset)


def split_clip_masks(left, right, source_cut):
    """Keep the right boundary ownership in sync with split_frames' source clock."""
    samples = normalized_masks(getattr(left, "recorded_channel_masks", {}))
    left.recorded_channel_masks = {
        u: [event for event in events if event[0] <= source_cut]
        for u, events in samples.items()}
    right.recorded_channel_masks = normalized_masks(samples, source_cut)


def masks_from_bitsets(bitsets, fps):
    """Compress generated ownership changes without a second dense frame array."""
    out = {}
    for universe, values in bitsets.items():
        events = []
        previous = None
        for index, bits in enumerate(values):
            if bits != previous:
                events.append([index / fps, base64.b64encode(bits.to_bytes(64, "big")).decode("ascii")])
                previous = bits
        out[int(universe)] = events
    return out


def feed_released_domains(controller):
    """An empty editor snapshot releases only domains already in this recording."""
    for universe in getattr(controller, "_rec_active_masks", {}):
        controller.feed_dmx_frame(universe, np.zeros(512, np.uint8), np.zeros(512, bool))


def should_record_domain(controller, universe, active):
    return np.any(active) or int(universe) in getattr(controller, "_rec_active_masks", {})


def append_released_frames(frames, masks, timestamp):
    """Capture missing Matrix domains as release, never as authored defaults."""
    from .models import DMXFrame
    present = {int(frame.universe) for frame, _ in frames}
    frames.extend((DMXFrame(u, timestamp, np.zeros(512, np.uint8)), np.zeros(512, bool))
                  for u in masks if u not in present)


@lru_cache(maxsize=512)
def _unpack(packed):
    result = np.unpackbits(np.frombuffer(base64.b64decode(packed), dtype=np.uint8)).astype(bool)
    result.flags.writeable = False
    return result


def recorded_mask_token(clip, universe, source_t):
    """Exact ownership event identity for memo; independent of sampled DMX rows."""
    samples = getattr(clip, "recorded_channel_masks", {}) or {}
    events = samples.get(int(universe)) or samples.get(str(universe))
    if not events:
        return None
    index = bisect_right(events, max(0., float(source_t)) + 1e-9,
                         key=lambda event: event[0]) - 1
    return events[index][1] if index >= 0 else ""


def recorded_mask_at(clip, universe, source_t):
    token = recorded_mask_token(clip, universe, source_t)
    if token is None:
        return None
    return _unpack(token) if token else _EMPTY_MASK


def with_source_mask_token(signature, clip, universe, source_t):
    token = recorded_mask_token(clip, universe, source_t)
    return signature if token is None else signature + (token,)


def source_mask_time(clip, universe, source_t, *, completed=False):
    samples = getattr(clip, "recorded_channel_masks", {}) or {}
    if not (samples.get(int(universe)) or samples.get(str(universe))):
        return None
    boundary = float(getattr(clip, 'source_duration', 0.) or 0.)
    return boundary if completed and boundary > 0. else source_t


def transition_source_mask_time(clip, universe, source_t, *, local_t, duration, hold_extension):
    return source_mask_time(clip, universe, source_t,
                            completed=not hold_extension and local_t >= duration)


def filter_active_mask(clip, universe, active, rule):
    """An output filter constrains ownership; it cannot undo a recorded release."""
    explicit = source_mask_time(clip, universe, 0.) is not None
    from .native_mask_math import filtered_active
    return filtered_active(active, rule, explicit)


def requires_source_mask_sampling(clip, universe, base_active):
    """The CE static union route cannot represent a changing recording mask."""
    samples = getattr(clip, "recorded_channel_masks", {}) or {}
    events = samples.get(int(universe)) or samples.get(str(universe))
    if not events:
        return False
    if len(events) > 1:
        return True
    mask = recorded_mask_at(clip, universe, 0.)
    return not np.array_equal(base_active, np.asarray(base_active, bool) & mask)


def ce_source_mask_eligible(original, clip, universe, engine):
    if not original(clip, universe, engine):
        return False
    compiled = engine._compiled_clip_universe(clip, universe)
    return not requires_source_mask_sampling(clip, universe, compiled["base_active"])
