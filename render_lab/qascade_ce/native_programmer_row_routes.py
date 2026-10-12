"""Prepared metadata routes; all ordinary mathematical queries use RowNative."""
from .native_programmer_row_queries import curve, mean, merged, one
from .native_programmer_row_qualification import numeric_curve, numeric_layers, numeric_universes, parsed
from . import native_programmer_row_clock_host as clock
from . import native_programmer_row_misc_host as misc
from . import native_programmer_row_sparse_host as sparse


def dispatch(context, name, arguments, original):
    host = arguments.get("self")
    if name == "automation_read_step":
        return misc.read(context, arguments["panel"])
    if name == "motor_8bit_to_16bit_pair":
        value = arguments["value"]
        return misc.motor(context, value) if parsed(value) else original()
    if name == "_traj_resolve_default":
        return misc.default(context, host, arguments["fid"], arguments["ch"])
    if name == "_traj_on_play_tick":
        return clock.tick(context, host, context.panel)
    if name == "_traj_on_scrub":
        return clock.scrub(context, host, arguments["val"])
    if name == "_compute_merged_value":
        if not numeric_layers(host):
            return original()
        return merged(context.native, host, arguments["fixture_id"], arguments["ch_type"], arguments["exclude_layers"])
    if name == "_get_merged_value":
        values = [host._values.get(fixture.id, {}).get(arguments["ch_type"], 0) for fixture in host._fixtures]
        if not all(type(value) in (bool, int, float) for value in values):
            return original()
        return mean(context.native, host, arguments["ch_type"])
    if name in ("_apply_curve", "_apply_curve_float"):
        values = tuple(arguments[key] for key in ("raw", "curve", "lo", "hi", "gamma", "steps"))
        return curve(context.native, values, name == "_apply_curve") if numeric_curve(values) else original()
    if name == "_mark_layer_sparse_from_universe_values":
        values = arguments["universe_values"]
        if not numeric_universes(values):
            return original()
        return sparse.sparse_universes(context, host, arguments["layer"], values)
    from .native_programmer_row_range_host import dispatch as range_dispatch
    from .native_programmer_row_physical_host import dispatch as physical_dispatch
    if name in ("_range_factor", "_spatial_factor", "_apply_channel_range", "_on_master_changed", "_set_range_state", "_on_range_strip_changed"):
        return range_dispatch(context, name, arguments, original)
    return physical_dispatch(context, name, arguments, original)
