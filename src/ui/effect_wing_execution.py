"""Run existing kernels on complete wing domains before broadcasting real members."""
from src.utils.wing_block import build_wing_block_layout, spatial_transform_active
from . import effect_order_execution as _order
from . import effect_width_execution as _width
from .effect_width_capabilities import grouped_history
from .effect_directions import PHASE_DIRECTIONS, normalize


def _count(params, key):
    try:
        return max(1, int(params.get(key, 1) or 1))
    except (TypeError, ValueError, OverflowError):
        return 1


def layout(block, params):
    return build_wing_block_layout(block, _count(params, 'wings'), _count(params, 'block_size'),
                                   mirror=bool(params.get('wing_mirror')),
                                   reverse=bool(params.get('wing_reverse')))


def history_blocks(blocks, params):
    if not spatial_transform_active(params):
        return blocks
    return [[item for unit in wing for item in unit]
            for block in blocks for _original, wing in layout(block, params)]


def _domain_params(original, virtual, params):
    if normalize(params.get('_app_direction')) in PHASE_DIRECTIONS:
        return _order.prepare_params(params, {}, ordered=virtual)
    work = dict(params)
    source = work.pop('_matrix_coords', None)
    work.pop('_matrix_x_span', None)
    if _order.coordinate_phase_enabled(params) and isinstance(source, dict):
        canonical = [source.get(str(unit[0][1].id)) for unit in original]
        if all(value is not None for value in canonical):
            work['_matrix_coords'] = {str(fi.id): value for (_index, fi), value in zip(virtual, canonical)}
            work['_matrix_x_span'] = (min(canonical), max(canonical))
    return work


def _versions(ctx, virtual, members, phase, delegate):
    from .effect_engine import _tagged_params
    fn, effect_t, fixture_defs, params, fps = ctx
    spread, subset_dt, ids_of, _routing = phase
    flat = [item for unit in members for item in unit]
    subs = ids_of(flat)
    tags = set(subs) if subs else {0}
    frames = {}
    for tag in tags:
        work = _tagged_params(params, tag) if subs else params
        time_ctx = (fn, effect_t + tag * subset_dt, spread, 0., fixture_defs, work, fps)
        frames[tag] = _width.time_values(time_ctx, virtual,
                                        (len(virtual), list(range(len(virtual)))), None, delegate)
        if work.get('_typed_width_finished'):
            params['_typed_width_finished'] = True
    return frames, subs


def _broadcast(ef, wing, frames, subs):
    cursor = 0
    for index, unit in enumerate(wing):
        for orig_i, _fixture in unit:
            tag = subs[cursor] if subs else 0
            if 0 <= orig_i < len(ef.values):
                ef.values[orig_i].update(frames[tag][index])
            cursor += 1


def run(ctx, ef, block, phase, delegate, *, prune=True):
    fn, effect_t, fixture_defs, params, fps = ctx
    persistent = grouped_history(fn)
    if persistent and prune:
        _width._prune_group_history(history_blocks([block], params), params)
    for original, wing in layout(block, params):
        virtual = [(index, unit[0][1]) for index, unit in enumerate(wing)]
        work = _domain_params(original, virtual, params)
        if persistent:
            work['_effect_group_key'] = _width.group_identity([item for unit in wing for item in unit], params)
        frames, subs = _versions((fn, effect_t, fixture_defs, work, fps), virtual, wing, phase, delegate)
        _broadcast(ef, wing, frames, subs)
        if work.get('_typed_width_finished'):
            params['_typed_width_finished'] = True
