"""Matrix adapters for the shared wing phases and existing panel/slide storage."""
from functools import wraps
from inspect import signature

from ...utils.wing_block import build_wing_block_units, normalize_wing_direction
from .. import wing_flag_controls as controls


def create(panel):
    return controls.create(panel, lambda: changed(panel), "_")


def changed(panel):
    from .matrix_slideshow_panel import _mark_panel_dirty
    panel._sync_wing_block_to_pixel_engine()
    _mark_panel_dirty(panel)


def restore(panel, engine, params, *, visible=False):
    for key, _label in controls.FLAGS:
        value = bool(params.get(key, False))
        setattr(panel, "_" + key, value)
        setattr(engine, key, value)
    if visible:
        controls.restore(panel, params, "_")


def sync_pixel(panel):
    engine = panel._pixel_engine
    if engine is None:
        return
    previous = (engine.wings, engine.block_size, engine.wing_direction, controls.values(engine))
    engine.wings = max(1, int(panel._wings_spin.value()))
    engine.block_size = max(1, int(panel._block_spin.value()))
    engine.wing_direction = normalize_wing_direction(panel._wing_direction)
    for key, value in controls.values(panel, "_").items():
        setattr(engine, key, value)
    current = (engine.wings, engine.block_size, engine.wing_direction, controls.values(engine))
    if previous != current:
        engine._wing_block_cache = {}
        if previous[:3] != current[:3] and hasattr(engine, "refresh_grid_layout"):
            engine.refresh_grid_layout()
        panel._reset_matrix_output_state(clear_pattern=True)
        if panel._mode == "pixel":
            panel._refresh_current_matrix_output()


def remap_slides(slides, ids, order, *, mirror, reverse):
    """Transform baked values once, including generators that write gray states."""
    wings, block_size, direction = order
    sources = build_wing_block_units(ids, wings, block_size, direction)
    targets = build_wing_block_units(ids, wings, block_size, direction,
                                    mirror=mirror, reverse=reverse)
    for slide in slides:
        original = dict(slide.states)
        for source, target in zip(sources, targets):
            level = max((original.get(fid, False) for fid in source), default=False)
            for fid in target:
                slide.states[fid] = level
    return slides


def initialize(panel):
    panel._wing_direction = "low_to_high"
    controls.restore(panel, {}, "_")
    panel._wing_direction_buttons = []


def restore_direction(panel, engine, params):
    restore(panel, engine, params)
    direction = params.get("wing_direction")
    if not direction:
        return
    panel._wing_direction = normalize_wing_direction(direction)
    engine.wing_direction = panel._wing_direction


def transform_generated(generate):
    """Opt-in value adapter around every existing sequence generator branch."""
    parameters = signature(generate)

    @wraps(generate)
    def transformed(*args, wing_mirror=False, wing_reverse=False, **kwargs):
        slides = generate(*args, **kwargs)
        if not wing_mirror and not wing_reverse:
            return slides
        call = parameters.bind(*args, **kwargs)
        call.apply_defaults()
        data = call.arguments
        order = (data["wings"], data["block_size"], data["wing_direction"])
        return remap_slides(slides, data["fixture_ids"], order,
                            mirror=wing_mirror, reverse=wing_reverse)

    return transformed
