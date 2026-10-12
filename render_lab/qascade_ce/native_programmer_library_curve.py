"""Small clock/shape routes; public callbacks and original owners stay ordered."""
from .native_programmer_library_metadata import assign_units, edges, flattened
from .native_programmer_library_metadata import resolve, scalar, segment, unit_groups
from .native_curve_scalar_install import _default as scalar_default
from .native_curve_scalar_metadata import accepts


def progress(native, source, time):
    bounds = edges(source)
    result = native.call("segment", scalar(1, source, time),
                         dict(n=len(bounds) - 1, a=bounds))
    index = result.indices[0].item()
    return index, segment(source, index), result.floats[0].item()


def path_prefix(native, source, time):
    value = source.value_at(time)
    bounds = edges(source)
    result = native.call("path", scalar(1, source, time),
                         dict(n=len(bounds) - 1, a=bounds, p0=value, flags=64))
    index = result.indices[0].item()
    return (result.data[0].item(), index, segment(source, index),
            result.floats[0].item(), result.floats[1].item())


def _sample_field(native, source, payload):
    units, offsets, bounds = payload
    options = dict(n=len(units), group_n=len(offsets) - 1, offsets=offsets,
                   p0=bounds[0], p1=bounds[1])
    if scalar_default(2, source) and accepts(2, source, 0.):
        result = native.call("shape", scalar(2, source), options)
        values = result.floats[:len(units)].tolist()
    else:
        native.compat("unsupported")
        result = native.call("unit_phase", scalar(2), options)
        values = [source._eval_phase(value) for value in result.floats[:len(units)].tolist()]
    return assign_units(units, values)


def _plain_subgroups(subgroups):
    return (type(subgroups) in (list, tuple)
            and all(type(ids) in (list, tuple)
                    and all(type(fid) in (str, int) for fid in ids) for ids in subgroups))


def _external_groups(native, source, payload):
    subgroups, owner, bounds = payload
    native.compat("unsupported")
    output = {}
    for subgroup in subgroups:
        units, offsets = flattened(unit_groups([subgroup], owner))
        output.update(_sample_field(native, source, (units, offsets, bounds)))
    return output


def shape_field(native, source, arguments):
    index, group, resolver = (arguments["seg_idx"], arguments["gid"],
                              arguments["resolve_group_segments"])
    bounds = source.segment_bounds()
    if not 0 <= index < len(bounds):
        return {}
    owner = segment(source, index)
    subgroups = resolve(resolver, group, owner.spatial_scope)
    if not _plain_subgroups(subgroups):
        return _external_groups(native, source, (subgroups, owner, bounds[index]))
    groups = unit_groups(subgroups, owner)
    units, offsets = flattened(groups)
    return _sample_field(native, source, (units, offsets, bounds[index]))


def _boundary(source, index):
    from src.ui.trajectory_curve import TrajectorySegment
    return source.boundaries[index] if index < len(source.boundaries) else TrajectorySegment()


def windows(native, source):
    bounds = edges(source)
    source._sync_boundaries()
    owners = [_boundary(source, i) for i in range(len(bounds))]
    result = native.call("windows", scalar(1, source),
                         dict(n=len(bounds) - 1, a=bounds,
                              b=[owner.transition or 0. for owner in owners]))
    count = len(bounds)
    output = []
    for i, owner in enumerate(owners):
        if result.flags[i]:
            kind = "start" if i == 0 else ("end" if i == count - 1 else "internal")
            output.append(dict(kind=kind, idx=i, t0=result.floats[count + i].item(),
                               t1=result.floats[2 * count + i].item(), bseg=owner))
    return output


def shape_time(native, source, time):
    result = native.call("shape_time", scalar(1, source, time), {})
    return result.floats[0].item(), result.floats[1].item()


def select_window(native, windows, time):
    result = native.call("window_select", scalar(2),
                         dict(n=len(windows), a=[row["t0"] for row in windows],
                              b=[row["t1"] for row in windows], p0=time))
    index = result.indices[0].item()
    if index < 0:
        return None, None, None
    return windows[index], result.floats[0].item(), result.floats[1].item()


def static_segment(native, source, time, duration):
    bounds = edges(source)
    result = native.call("static_select", scalar(2),
                         dict(n=len(bounds) - 1, a=bounds, p0=time, p1=duration))
    return result.indices[0].item()
