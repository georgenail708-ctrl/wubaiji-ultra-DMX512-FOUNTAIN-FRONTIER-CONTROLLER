"""Original fixture/channel metadata with native retained-K2 preparation."""
from types import SimpleNamespace

import numpy as np

from . import native_legacy_direction_prepare as prepare
from . import native_source_motion as motion

A_FIELDS = ("row", "e", "clip", "u", "track", "compiled", "groups", "batch_count",
            "group_pause", "single_raw", "sdi", "sdo", "fi", "fo", "dur",
            "in_dir", "out_dir", "ratio_in", "ratio_out")
B_FIELDS = ("row", "e", "clip", "u", "track", "groups", "group_pause",
            "single_raw", "sdi", "sdo", "fi", "fo", "in_dir", "out_dir")


def arguments(names, args, kwargs):
    values = dict(zip(names, args))
    values.update(kwargs)
    return SimpleNamespace(**values)


def _fixture_groups(state, fixtures):
    for edge in ("in", "out"):
        direction = getattr(state, edge + "_dir")
        if direction == "none":
            continue
        values = state.e._sequential_fixture_group_data(state.clip, fixtures, direction,
            state.groups, track=state.track, proj=state.project)
        for field, value in zip(("gi", "gp", "gs"), values):
            state.row[field + "_" + edge] = value


def _type_catalog(state, fixtures, spans):
    definitions = state.e._fixture_defs_index(state.project)
    mappings = state.e._clip_fixture_type_index_map(fixtures, spans, definitions)
    present = set()
    for mapping in mappings:
        present.update(mapping.keys())
    return mappings, sorted(present)


def _type_entries(mappings, names):
    result = []
    for fixture, mapping in enumerate(mappings):
        for index, name in enumerate(names):
            result.extend((fixture, index, int(offset)) for offset in mapping.get(name, []))
    return result


def _ratios(state, fixtures, spans):
    if not (state.ratio_in or state.ratio_out):
        return
    mappings, names = _type_catalog(state, fixtures, spans)
    state.row["ntypes"] = len(names) if names else -1
    state.row["nentries"] = 0
    if not names:
        return
    for edge in ("in", "out"):
        ratios = getattr(state, "ratio_" + edge)
        state.row["ratio_" + edge] = np.asarray([ratios.get(name, 1.) for name in names], np.float64)
    entries = _type_entries(mappings, names)
    state.row["nentries"] = len(entries)
    if entries:
        state.row["entries"] = np.asarray(entries, np.int32)


def path_a(builder, *args, **kwargs):
    state = arguments(A_FIELDS, args, kwargs)
    state.project = builder.proj
    fixtures = list(state.compiled.get("sorted_fixtures", []) or [])
    spans = list(state.compiled.get("spans", []) or [])
    state.row["K"] = len(fixtures)
    if not fixtures:
        return
    state.row["spans"] = np.asarray(spans, np.int32)
    _fixture_groups(state, fixtures)
    pauses = state.e._clip_sequential_pauses(state.clip, state.groups, state.group_pause)
    state.row.update(prepare.fixture(state.row, state.batch_count, pauses))
    offsets = state.e._clip_sequential_offsets(state.row["per_group"], pauses, state.batch_count)
    state.row["bo_a"] = np.asarray(offsets, np.float32)
    state.row["bo_a_out"] = prepare.scale_offsets(state.row["bo_a"], state.row["out_scale"])
    state.row["gidx_c"] = state.e._sequential_group_indices_for_fixtures(state.clip,
        fixtures, state.groups, track=state.track, proj=builder.proj, direction=state.in_dir)
    _ratios(state, fixtures, spans)


def _channel_groups(state, count):
    groups, width = prepare.group_counts(count, state.groups)
    for edge in ("in", "out"):
        position = state.e._compute_direction_positions(getattr(state, edge + "_dir"), count)
        state.row["pos_" + edge] = position
        gi, gp, gs = motion.groups(position, groups, False)
        state.row["gppos_" + edge] = gp.copy()
        state.row["max_step_" + edge] = width
        for field, value in zip(("gidx", "gpos", "gsize"), (gi, gp, gs)):
            state.row[field + "_" + edge] = value
    return groups


def path_b(builder, *args, **kwargs):
    state = arguments(B_FIELDS, args, kwargs)
    active = state.e._get_clip_active_channels(state.clip, state.u, track=state.track)
    state.row["K"] = len(active)
    if not active:
        return
    state.row["channels"] = np.asarray(active, np.int32)
    groups = _channel_groups(state, len(active))
    batches = state.e._clip_sequential_batch_count(state.clip, groups)
    state.row["batch_count_b"] = batches
    for edge in ("in", "out"):
        pauses = state.e._clip_sequential_pauses(state.clip, groups, state.group_pause)
        offsets = state.e._clip_sequential_offsets(state.row["pg_b_" + edge], pauses, batches)
        state.row["bo_b_" + edge] = np.asarray(offsets, np.float32)
    state.row["gidx_c"] = state.e._sequential_group_indices_for_positions(
        state.row["pos_in"], state.groups)
