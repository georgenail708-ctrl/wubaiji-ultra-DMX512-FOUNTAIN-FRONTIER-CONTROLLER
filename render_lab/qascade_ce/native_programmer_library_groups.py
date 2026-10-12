"""Original membership/wing units; dense native rank/reveal/morph calculations."""
from .native_programmer_library_metadata import assign_units, group_options, scalar
from .native_programmer_library_types import UnsupportedNative

DIRECTIONS = dict(whole=0, ltr=0, rtl=1, ftb=2, btf=3, ctr=4,
                  rtc=5, cw=6, ccw=7, alt=8)


def _coordinates(ids, positions):
    rows = []
    for fid in ids:
        value = (positions or {}).get(str(fid))
        rows.append((0., 0.) if value is None else (value[0], value[1]))
    if all(type(v) is float for row in rows for v in row):
        return dict(a=[row[0] for row in rows], b=[row[1] for row in rows])
    if not all(type(v) in (bool, int, float, str, bytes, type(None))
               for row in rows for v in row):
        raise UnsupportedNative("External coordinate numeric conversion")
    return dict(values=[v for row in rows for v in row], flags=4)


def direction(native, ids, name, positions):
    code = DIRECTIONS.get(name, 0)
    if not ids or code == 0:
        return list(ids)
    if code in range(2, 8):
        return native.sort_binding.direction(ids, name, positions)
    options = dict(n=len(ids), flags=code << 8)
    result = native.call("direction", scalar(2), options)
    return [ids[index] for index in result.indices[:len(ids)]]


def _units(ids, owner):
    from src.ui import trajectory_wing_adapter
    return trajectory_wing_adapter.units(ids, owner)


def reveal(native, arguments):
    ids = [str(fid) for fid in arguments["ids_left_to_right"] if str(fid)]
    owner = arguments["seg"]
    if not ids:
        return []
    if owner is None or owner.direction == "whole":
        return list(ids)
    ordered = direction(native, ids, owner.direction, arguments["positions"])
    units = _units(ordered, owner)
    if not units:
        return list(ids)
    options = group_options(owner, len(units), arguments["f"], arguments["seg_dur"] or 0.)
    result = native.call("reveal", scalar(2, argument=arguments["f"]), options)
    return [str(fid) for unit, active in zip(units, result.flags)
            if active for fid in unit]


def morph(native, arguments):
    from src.ui.trajectory_curve import TrajectorySegment
    ids = [str(fid) for fid in arguments["ids"] if str(fid)]
    if not ids:
        return {}
    owner = arguments["bseg"] or TrajectorySegment()
    ordered = direction(native, ids, owner.direction, arguments["positions"])
    units = _units(ordered, owner) or [[fid] for fid in ordered]
    options = group_options(owner, len(units), arguments["f"], arguments["window_dur"])
    result = native.call("morph", scalar(2, argument=arguments["f"]), options)
    output = assign_units(units, result.floats[:len(units)].tolist())
    for fid in ids:
        output.setdefault(fid, result.floats[len(units)].item())
    return output


def effective(native, owner):
    options = dict(groups=owner.groups, batches=owner.group_batches)
    result = native.call("effective", scalar(2), options)
    value = result.report.result
    if value.kind == 1:
        return result.prepared.owners[value.selector]
    return 1


def partition(native, n, batch_count):
    options = dict(n=n, groups=batch_count, batches=0)
    result = native.call("partition", scalar(2), options)
    return result.indices[:n].tolist(), result.indices[n].item()


def offsets(native, owner, per_group, span=False):
    count = effective(native, owner)
    n = count if type(count) in (int, bool) else 0
    options = dict(n=n, group_n=len(owner.group_pauses), a=owner.group_pauses,
                   groups=owner.groups, batches=owner.group_batches,
                   p0=per_group, p2=owner.group_pause, flags=32 if span else 0)
    result = native.call("offsets", scalar(2), options)
    if not span:
        return result.floats[:count].tolist(), None
    value = result.value
    if value.kind == 2:
        result_span = int.from_bytes(result.integer_bytes[value.offset:value.offset + value.size], "little", signed=True)
    else:
        result_span = value.number
    return [], result_span
