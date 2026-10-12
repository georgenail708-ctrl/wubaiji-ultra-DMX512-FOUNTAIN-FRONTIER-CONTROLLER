"""Preserve retained direction metadata/state order; all row math is native."""
import numpy as np

from . import native_legacy_direction_prepare as prepare
from .native_legacy_direction_paths import A_FIELDS, B_FIELDS, arguments
from .native_source_motion_clock import sample_endpoint
from .native_source_motion_types import CURVES
from .timeline_ctx import _STRAT_IDX

BUILD_FIELDS = ("clip", "u", "track", "compiled", "target_universes")
CORE_FIELDS = {"dur": 0, "fi_raw": 1, "fo_raw": 2, "fi": 3, "fo": 4,
    "sdi_raw": 5, "sdo_raw": 6, "sdi": 7, "sdo": 8, "single_raw": 9,
    "pg_b_in": 11, "pg_b_out": 12, "pause_sum": 15}


def _mode(clip):
    value = str(getattr(clip, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
    return value if value in ("parallel", "sequential") else "parallel"


def _timing(state):
    e, clip = state.e, state.clip
    state.in_dir = e._get_clip_fade_in_direction(clip)
    state.out_dir = e._get_clip_fade_out_direction(clip)
    state.detail = e._clip_sequential_timing_detail(clip)
    state.group_timing = e._clip_sequential_group_timing(clip)
    numeric = prepare.core(clip, state.detail[2], _mode(clip))
    state.groups, state.group_pause = int(numeric[14]), float(numeric[10])
    state.row = {name: float(numeric[index]) for name, index in CORE_FIELDS.items()}
    state.row.update(seq_gains=int(numeric[16]), seq_enabled=int(state.detail[0]),
        groups=state.groups, batch_count=state.detail[4], single=state.detail[3])
    state.row["seq_span"] = _span(state, state.detail[0], state.group_timing[1:])
    state.row["sequence_span"] = _span(state, bool(numeric[13]),
        (state.groups, state.group_pause, state.row["single_raw"]))


def _span(state, enabled, values):
    if not enabled:
        return 0.
    return state.e._clip_sequential_span(state.clip, *values)


def _transition_metadata(state):
    e, clip, row = state.e, state.clip, state.row
    row["src_dur"] = e._clip_source_duration(clip, row["dur"])
    row["phase_amount"] = e._clip_motor_phase_amount(clip)
    row["motor_phase_enabled"] = int(bool(getattr(clip, "motor_transition_phase_enabled", False)))
    row["parallel_remap"] = int(not row["seq_enabled"] and row["motor_phase_enabled"])
    row["dir_in_active"], row["dir_out_active"] = int(state.in_dir != "none"), int(state.out_dir != "none")
    row["te"] = int(e._clip_transition_enabled(clip))
    for edge, fade_in in (("fi", True), ("fo", False)):
        row["curve_" + edge] = CURVES[e._clip_transition_curve(clip, fade_in=fade_in)]
        row["strategy_" + edge] = _STRAT_IDX[e._clip_transition_strategy(clip, fade_in=fade_in)]
    row["path"] = 0 if list(state.compiled.get("u_fixtures", []) or []) else 1


def _selected_ratios(engine, source):
    pairs = list((source or {}).items())
    flags = prepare.ratio_flags([float(value) for _, value in pairs])
    return {engine._norm_ratio_channel_type(key): float(value)
            for (key, value), selected in zip(pairs, flags) if selected}


def _pointers(state):
    row, e, clip = state.row, state.e, state.clip
    state.ratio_in = _selected_ratios(e, getattr(clip, "fade_type_ratio_in", {}))
    state.ratio_out = _selected_ratios(e, getattr(clip, "fade_type_ratio_out", {}))
    lut = e._seq_delay_ease_lut(getattr(clip, "seq_delay_curve", None))
    if lut is not None:
        row["ease_lut"] = lut
    anchor = e._get_fade_in_anchor(clip, state.u, track=state.track, proj=state.project)
    if anchor is not None:
        row["anchor"] = anchor
    target = e._get_fade_out_target(state.u, proj=state.project)
    if target is not None:
        row["target"] = target
    offset_step = state.detail[3] if state.detail[5] is None else state.detail[5]
    row["offsets"] = np.asarray(e._clip_sequential_offsets(
        offset_step, state.detail[2], state.detail[4]), np.float64)


def _source_metadata(state):
    row, e, clip = state.row, state.e, state.clip
    mask = e._clip_base_order_channel_mask(clip, state.u)
    if mask is not None:
        row["base_order_mask"] = mask
    raw = e._compiled_clip_frame_data(state.compiled, sample_endpoint(row["src_dur"]))
    if raw is not None:
        row["end_raw"] = raw
    universes = e._clip_frame_universes(clip)
    row["remap_universe_ok"] = int(not (universes and state.u not in universes))


def _path_arguments(state):
    values = dict(vars(state))
    values.update(state.row)
    return values


def _finish(builder, state):
    values = _path_arguments(state)
    if state.row["path"] == 0:
        builder._build_path_a(*(values[name] for name in A_FIELDS))
    else:
        builder._build_path_b(*(values[name] for name in B_FIELDS))
    builder._fill_remap(state.row, state.e, state.clip, state.u, state.track,
        state.compiled, state.target_universes, state.detail[0], state.row["parallel_remap"],
        state.in_dir, state.row["src_dur"])
    return state.row


def build(builder, *args, **kwargs):
    state = arguments(BUILD_FIELDS, args, kwargs)
    state.e, state.project = builder.engine, builder.proj
    _, enabled = prepare.duration(getattr(state.clip, "duration", 0.) or 0.)
    if not enabled:
        return None
    float(getattr(state.clip, "fade_in", 0.) or 0.)
    float(getattr(state.clip, "fade_out", 0.) or 0.)
    if not state.e._clip_has_directional_fade(state.clip):
        return None
    _timing(state)
    _transition_metadata(state)
    _pointers(state)
    _source_metadata(state)
    return _finish(builder, state)
