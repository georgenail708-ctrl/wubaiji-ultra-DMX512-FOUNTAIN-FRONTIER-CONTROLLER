"""Generate Cue frames with the original Programmer address/value mapping.

Only present author keys own channels, including zero. Per-frame ownership is
packed as change events shared with the recorders; defaults remain unclaimed.
Reference: MA dot2 Zero/Off semantics, NumPy packbits; no new output writer.
"""
from types import SimpleNamespace

import numpy as np

from src.core.models import DMXFrame
from src.core.recorded_channel_masks import masks_from_bitsets


def render_cue_frames(panel, cue, fixtures, timing, light_predicate):
    ctx = SimpleNamespace(panel=panel, cue=cue, timing=timing,
        light_predicate=light_predicate, frames={}, anchors={}, active={}, bits={},
        effect_maps=(cue.effects or {}).get("_timeline_frames_by_fixture"))
    for fixture in fixtures:
        _fixture_frames(ctx, fixture)
    return ctx.frames, ctx.active, masks_from_bitsets(ctx.bits, timing["fps"])


def _ensure_universe(ctx, universe):
    if universe in ctx.frames:
        return
    timing = ctx.timing
    ctx.frames[universe] = [DMXFrame(universe, i / timing["fps"], np.zeros(512, np.uint8))
                            for i in range(timing["count"])]
    ctx.bits[universe] = [0] * timing["count"]
    ctx.anchors[universe] = ctx.panel._sample_transition_anchor_frame(universe, timing["start"])


def _fixture_frames(ctx, fixture):
    definition = ctx.panel._fixture_defs_cache.get(fixture.def_id)
    if definition is None:
        return
    _ensure_universe(ctx, fixture.universe)
    physical = list(getattr(definition, "channels", []) or [])
    logical = list(physical)
    if ctx.light_predicate(definition) and "dimmer" not in logical:
        logical.append("dimmer")
    for index, channel in enumerate(logical):
        addresses = _addresses(fixture, channel, index, physical)
        if addresses is not None:
            _write_channel(ctx, fixture, channel, addresses)


def _addresses(fixture, channel, index, physical):
    if channel == "placeholder":
        return None
    if channel == "dimmer" and channel not in physical:
        return []
    address = fixture.start_channel + index - 1
    if index < len(physical) and 0 <= address < 512:
        return [(fixture.universe, address)]
    return None


def _source_values(ctx, fixture_id, index):
    if ctx.effect_maps:
        source = min(int(index * len(ctx.effect_maps) / ctx.timing["count"]), len(ctx.effect_maps) - 1)
        return ctx.effect_maps[source].get(fixture_id, {})
    return ctx.cue.values.get(fixture_id, {})


def _write_channel(ctx, fixture, channel, addresses):
    for index in range(ctx.timing["count"]):
        values = _source_values(ctx, fixture.id, index)
        if channel not in values:
            continue
        try:
            raw = float(values[channel] or 0)
        except (ValueError, TypeError):
            raw = 0.
        outputs = ctx.panel._expand_channel_output(channel, raw, addresses,
                                                   ctx.panel._addr_cache.get(fixture.id, {}))
        _write_outputs(ctx, outputs, index)


def _write_outputs(ctx, outputs, index):
    for universe, channel, raw in outputs:
        universe, channel = int(universe), int(channel)
        if not 0 <= channel < 512:
            continue
        _ensure_universe(ctx, universe)
        ctx.active.setdefault(universe, set()).add(channel + 1)
        ctx.bits[universe][index] |= 1 << (511 - channel)
        value = float(raw)
        fade = ctx.timing["fade_count"]
        if index < fade:
            anchor = max(0, int(ctx.anchors[universe][channel]))
            value = anchor + (value - anchor) * index / max(1, fade)
        ctx.frames[universe][index].data[channel] = int(np.clip(value, 0, 255))
