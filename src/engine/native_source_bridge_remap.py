"""Existing copy-map metadata is retained; CE owns every row's source math."""
from .native_source_bridge import modules, table


def render(engine, clip, copy_map, context):
    from . import playback_engine as public
    motion, clock = modules()
    compiled = context.get("compiled_by_universe") or {}
    universes = list(dict.fromkeys(int(row[3]) for row in copy_map))
    tables = [table(engine, clip, universe, compiled.get(universe)) for universe in universes]
    slots = {universe: index for index, universe in enumerate(universes)}
    rows = []
    for source in copy_map:
        target, start, count, universe, group = source[:5]
        relative = source[5] if len(source) >= 7 else 0.
        phase = source[7] if len(source) >= 8 else False
        rows.append((target, start, count, slots[int(universe)], group, int(bool(phase)), relative))
    profile = clock.profile(engine, clip)
    config = dict(context)
    config.update(tables=tables, rows=rows, accel=0., cruise=0., decel=0.,
                  force_enabled=int(profile is not None),
                  cyclic=int(public._sequential_source_is_cyclic(engine, clip, context["universe"])))
    if profile is not None:
        config.update(accel=profile[0], cruise=profile[1], decel=profile[2])
    return motion.remap(config)
