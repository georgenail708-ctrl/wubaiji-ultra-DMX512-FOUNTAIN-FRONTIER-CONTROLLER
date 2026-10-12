"""Prepare current public clip semantics for bounded native frame math.

References: existing playback_engine, NumPy contiguous ndarray ownership, and
the fixed CPython ctypes boundary in ai-memory/_references/r211-prepared-k2-adoption.md.
Stateful source/LT/audio/filter preparation remains in original task order.
"""
from types import SimpleNamespace

import numpy as np

from src.core.recorded_channel_masks import filter_active_mask
from src.core.native_mask_math import mask_or, mask_count
from .source_mask_memo import remember as remember_source_memo
from .prepared_score_scalars import score_scalars
from .source_sampling_math import endpoint
from src.engine.audio_mod_clip_processor import apply_clip_filters_with_audio
from src.engine.clip_output_filters import filters_active
from src.engine.clip_takeover import (
    clip_pre_arrival_mask, fade_in_branch_taken,
    transition_boost_mask,
)

INPUT_FIELDS = (
    "clip u track proj now compiled raw source_t hold_extension local_t gain "
    "clip_has_directional_fade is_in_fade_out is_in_directional_in is_in_fade_in "
    "sequential_source_remapped fixture_defs_by_id_global _dr_source_duration "
    "_dr_light_tracking _hold_key _dr_clip_id _start_t _track_order _dr_priority "
    "_transition_boost total_priority _dirty_on _dr_fast_ok _memo_key sig_item "
    "_dr_chain _dr_record_source_enabled"
).split()


def candidate_input(values):
    """Explicit task tuple; avoid materializing the renderer's large locals dict."""
    return SimpleNamespace(**dict(zip(INPUT_FIELDS, values, strict=True)))


def _direction_bundle(engine, item, fixtures, kw):
    bundle = getattr(engine, "_ce_source_direction_bundle", None)
    if bundle is not None:
        return bundle(item.clip, item.local_t, item.u, fixtures, **kw)
    gains = engine._clip_channel_fade_gains(item.clip, item.local_t, item.u, fixtures, **kw)
    return gains, None, gains > 1e-6


def _direction_math(engine, item):
    clip, compiled, u = item.clip, item.compiled, item.u
    fixtures = list(compiled.get("u_fixtures", []) or [])
    kw = dict(track=item.track, fixture_defs_by_id=item.fixture_defs_by_id_global,
              proj=item.proj, compiled=compiled)
    gains, completed_native, arrived = _direction_bundle(engine, item, fixtures, kw)
    base, end, completed = None, None, None
    if item.is_in_fade_out:
        base = engine._get_fade_out_target(u, proj=item.proj)
    elif item.is_in_directional_in:
        base = engine._get_fade_in_anchor(clip, u, track=item.track, proj=item.proj)
        if not item.sequential_source_remapped:
            completed = completed_native
            if completed is None:
                completed = engine._clip_completed_sequential_channel_mask(
                    clip, item.local_t, u, fixtures, **kw)
            if mask_count(completed):
                end = engine._compiled_clip_frame_data(
                    compiled, endpoint(item._dr_source_duration))
            if end is None:
                completed = None
    return dict(gains=gains, base=base, end=end, completed=completed), arrived


def _math_spec(engine, item):
    if item.hold_extension:
        return dict(gains=np.ones(512, np.float32), base=None), None
    if item.clip_has_directional_fade:
        return _direction_math(engine, item)
    base = None
    if item.is_in_fade_in:
        base = engine._get_fade_in_anchor(
            item.clip, item.u, track=item.track, proj=item.proj)
    elif item.is_in_fade_out:
        base = engine._get_fade_out_target(item.u, proj=item.proj)
    return dict(gains=np.full(512, item.gain, np.float32), base=base), None


def _float_math(raw, spec):
    values = np.asarray(raw, dtype=np.float32)
    base, gains = spec.get("base"), spec["gains"]
    data = values * gains if base is None else base + gains * (values - base)
    end, completed = spec.get("end"), spec.get("completed")
    if end is not None and completed is not None:
        mask = np.asarray(completed, dtype=bool)
        tail = np.asarray(end, dtype=np.float32)
        data[mask] = base[mask] + gains[mask] * (tail[mask] - base[mask])
    return data


def _active(engine, item, arrived):
    compiled, clip, u = item.compiled, item.clip, item.u
    active = engine._clip_active_mask_for_rendered_raw(
        clip, u, item.raw,
        compiled.get("base_active", engine._clip_channel_active_mask(clip, u)),
        source_t=item.source_t, hold_extension=item.hold_extension)
    if item._dr_light_tracking:
        active = mask_or(active, compiled.get("light_mask", np.zeros(512, bool)))
    rule = compiled.get("rule_active")
    if rule is not None:
        active = filter_active_mask(clip, u, active, np.asarray(rule, bool))
    if item._hold_key is not None:
        active = engine._hold_consumed_active_for_item(item._hold_key, int(u), active)
    active = engine._takeover_consumed_active_for_item(item._dr_clip_id, int(u), active)
    return active, clip_pre_arrival_mask(active, arrived, item.is_in_directional_in)


def _score_spec(engine, item, active):
    idx = engine._compiled_clip_frame_index(item.compiled, item.source_t)
    changes = item.compiled.get("last_change_time")
    result = dict(start=float(item._start_t), **score_scalars(engine, item, idx),
                  changes_mode=0, changes=np.zeros(512, np.float32))
    if idx < 0:
        return result
    if changes is not None and idx < len(changes) and changes[idx].dtype == np.float32:
        result["changes"] = changes[idx]
        return result
    result.update(changes_mode=1, base_scores=engine._compiled_clip_channel_change_scores(
        item.clip, int(item.u), item.source_t, item.now, item._start_t, active,
        item.compiled, hold_extension=item.hold_extension))
    return result


def _descriptor(engine, item, spec, active, prearrival):
    owned = {name: value.copy() if isinstance(value, np.ndarray) else value
             for name, value in spec.items()}
    descriptor = dict(owned, raw=np.asarray(item.raw, np.uint8).copy(),
                      active=active.copy(), prearrival=prearrival,
                      mode=int(spec.get("base") is not None),
                      ramp=int(fade_in_branch_taken(item.clip_has_directional_fade,
                          item.is_in_directional_in, item.is_in_fade_in, item.is_in_fade_out)),
                      boost=item._transition_boost)
    descriptor.update(_score_spec(engine, item, active))
    if item._transition_boost > 0.0:
        descriptor["idle"] = engine._get_fade_out_target(item.u, proj=item.proj).copy()
    return descriptor


def _requires_ordered_filter(item):
    audio = getattr(item.proj, "audio_modulation_state", {}) or {}
    return audio.get("processor_mode") == "chain" or filters_active(item.clip)


def evaluate_python(descriptor):
    """Pure-stage fallback; never replay a stateful source/filter or consumer."""
    data = descriptor.get("override")
    if data is None:
        data = _float_math(descriptor["raw"], descriptor)
    data = np.clip(data, 0.0, 255.0)
    with np.errstate(invalid="ignore"):
        quantized = data.astype(np.uint8)
    active = descriptor["active"]
    scores = np.full(512, -np.inf, np.float64)
    if descriptor["changes_mode"]:
        scores[active] = descriptor["base_scores"][active]
    else:
        scores[active] = descriptor["start"] + descriptor["changes"][active].astype(
            np.float64) / descriptor["rate"]
    scores[active] = np.minimum(scores[active], descriptor["hold_cap"]) + descriptor["tie"]
    if descriptor["boost"] > 0:
        mask = transition_boost_mask(active, data, descriptor["idle"],
                                     descriptor["raw"], bool(descriptor["ramp"]))
        scores[mask] += descriptor["boost"]
    mask = active & descriptor["prearrival"]
    scores[mask] += descriptor["preboost"]
    return data, quantized, scores


def _store_memo(engine, item, candidate):
    if not (item._dirty_on and item._dr_fast_ok):
        return
    data, active, anchor, scores = candidate
    engine._dirty_render_item_memo[item._memo_key] = dict(
        sig=item.sig_item, fast=True, data=data.copy(), active=active.copy(),
        scores=scores, anchor=anchor)
    remember_source_memo(engine, (item._memo_key, item.clip, item.u, item.source_t, item.hold_extension),
                         engine._dirty_render_item_memo[item._memo_key])
    if len(engine._dirty_render_item_memo) > 8192:
        engine._dirty_render_item_memo.pop(next(iter(engine._dirty_render_item_memo)))


def _append(item, rows, candidate):
    main, meta, record, registry = rows
    universe = int(item.u)
    main.setdefault(universe, []).append(candidate)
    meta.setdefault(universe, []).append((item._dr_clip_id, item._dr_chain))
    if item._hold_key is not None:
        registry.append((universe, item._hold_key, candidate, item._dr_chain))
    if item._dr_record_source_enabled:
        # Both groups are read-only until resolution; final record outputs copy.
        record.setdefault(universe, []).append(candidate)


def append_candidate(engine, frame, item, rows):
    spec, arrived = _math_spec(engine, item)
    ordered = frame is None or _requires_ordered_filter(item)
    if ordered:
        source = dict(spec, raw=np.asarray(item.raw, np.uint8),
                      mode=int(spec.get("base") is not None),
                      active=np.zeros(512, np.uint8), changes=np.zeros(512, np.float32))
        data = frame.float_values(source) if frame is not None else _float_math(item.raw, spec)
        filtered = apply_clip_filters_with_audio(engine, item.clip, item.u, data,
            (item.track, item.proj, item.now, item.compiled, item.raw, item.source_t,
             item.hold_extension, item.local_t))
        spec["override"] = filtered
        spec["native_transformed"] = frame is not None and not frame.failed
    active, prearrival = _active(engine, item, arrived)
    if not mask_count(active):
        return
    descriptor = _descriptor(engine, item, spec, active, prearrival)
    anchor = engine._get_clip_anchor(item.clip, item.u, active)
    data, scores = np.empty(512, np.uint8), np.empty(512, np.float64)
    candidate = (data, active.copy(), anchor, scores)
    _append(item, rows, candidate)
    def complete(result):
        np.copyto(data, result[1])
        np.copyto(scores, result[2])
        _store_memo(engine, item, candidate)
    if frame is None:
        complete(evaluate_python(descriptor))
    else:
        frame.add(descriptor, complete)
