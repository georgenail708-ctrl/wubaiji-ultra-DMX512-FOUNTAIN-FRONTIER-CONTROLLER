"""Reuse Engine's original frame/time dispatch; finish before logical broadcasts."""
from . import effect_width_field as _field
from . import effect_order_execution as _order_execution
from .effect_width_capabilities import grouped_history


def _finish_values(values, params):
    finished = _field.finish(values, params)
    if params.get("_typed_width"):
        params["_typed_width_finished"] = True
    return finished


def direct_values(ctx, call_sorted, layout):
    from .effect_engine import EffectFrame
    fn, effect_t, fixture_defs, params, fps = ctx
    frame_len, read_indices = layout
    tmp = EffectFrame(t=effect_t)
    tmp.values = [{} for _ in range(frame_len)]
    fn(tmp, effect_t, call_sorted, fixture_defs, params, fps)
    values = [dict(tmp.values[index]) if 0 <= index < frame_len else {} for index in read_indices]
    return _finish_values(values, params)


def time_values(ctx, call_sorted, layout, subs, delegate):
    values = delegate(ctx, call_sorted, layout[0], layout[1], subs)
    return _finish_values(values, ctx[5])


def group_identity(block, params):
    return (tuple(sorted(str(fi.id) for _index, fi in block)), str(params.get("ch_type", "")))


def _group_params(block, params):
    return dict(params, _effect_group_key=group_identity(block, params))


def _prune_group_history(blocks, params):
    state = params.get("_audio_runtime")
    history = getattr(state, "group_history", None)
    if history is None:
        return
    keep = {group_identity(block, params) for block in blocks}
    for key in list(history):
        if key[0] not in keep:
            del history[key]


def _group_values(ctx, virtual, phase, delegate):
    fn, effect_t, fixture_defs, params, fps = ctx
    spread, subset_dt, subset_ids, routing = phase
    n = len(virtual)
    layout = (n, list(range(n)))
    if spread or routing(virtual):
        return time_values((fn, effect_t, spread, subset_dt, fixture_defs, params, fps),
                           virtual, layout, subset_ids(virtual), delegate)
    return direct_values(ctx, virtual, layout)


def _run_group(ctx, ef, block, phase, delegate):
    from .effect_engine import build_effect_spatial_groups
    from . import effect_wing_execution as wings
    from src.utils.wing_block import spatial_transform_active
    fn, effect_t, fixture_defs, params, fps = ctx
    if spatial_transform_active(params):
        wings.run(ctx, ef, block, phase, delegate, prune=False)
        return
    units = build_effect_spatial_groups(block, params)
    virtual = [(k, unit[0][1]) for k, unit in enumerate(units)]
    work = _group_params(block, params) if grouped_history(fn) else params
    if _order_execution.per_shape_phase_enabled(params):
        work = _order_execution.prepare_params(work, {}, ordered=virtual)
    values = _group_values((fn, effect_t, fixture_defs, work, fps), virtual, phase, delegate)
    for k, unit in enumerate(units):
        for orig_i, _fi in unit:
            if 0 <= orig_i < len(ef.values) and k < len(values):
                ef.values[orig_i].update(values[k])


def run_groups(ctx, ef, blocks, phase, delegate):
    from . import effect_wing_execution as wings
    fn, _effect_t, _fixture_defs, params, _fps = ctx
    persistent = grouped_history(fn)
    if persistent:
        _prune_group_history(wings.history_blocks(blocks, params), params)
    for block in blocks:
        _run_group(ctx, ef, block, phase, delegate)


def profile_complete(ef, sorted_fixtures, params, per_shape_units):
    if per_shape_units:
        return True
    if not params.get("_typed_width"):
        return False
    if not params.get("_typed_width_finished"):
        indices = [index for index, _fi in sorted_fixtures]
        rows = [ef.values[index] for index in indices]
        for index, values in zip(indices, _finish_values(rows, params)):
            ef.values[index].update(values)
    return True
