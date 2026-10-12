"""Container-only trajectory evaluation; native domain performs numeric stages."""
from .native_programmer_library_curve import path_prefix, select_window, shape_time
from .native_programmer_library_curve import static_segment
from .native_programmer_library_metadata import resolve, scalar, targets


def quantize(native, values):
    values = list(values)
    result = native.call("quantize", scalar(2), dict(n=len(values), a=values))
    return result.data[:len(values)].tolist()


def path(native, source, time, resolver, positions):
    byte, _index, owner, progress, duration = path_prefix(native, source, time)
    output = {}
    for group, channels in targets(source):
        for ids in resolve(resolver, group, owner.spatial_scope):
            reached = source.reached_fixture_ids(ids, owner, progress, duration, positions)
            for fid in reached:
                slot = output.setdefault(str(fid), {})
                for channel in channels:
                    slot[channel] = byte
    return output


def _static(native, source, time, duration, resolver):
    output = {}
    index = static_segment(native, source, time, duration)
    for group, channels in targets(source):
        field = source._shape_segment_field(index, group, resolver)
        data = quantize(native, field.values())
        for fid, byte in zip(field, data):
            slot = output.setdefault(str(fid), {})
            for channel in channels:
                slot[channel] = byte
    return output


def _fields(source, window, group, resolver):
    index, kind = window["idx"], window["kind"]
    if kind == "start":
        return None, source._shape_segment_field(0, group, resolver)
    if kind == "end":
        return source._shape_segment_field(index - 1, group, resolver), None
    return (source._shape_segment_field(index - 1, group, resolver),
            source._shape_segment_field(index, group, resolver))


def _alphas(source, payload):
    window, group, resolver, progress, duration, positions = payload
    output = {}
    owner = window["bseg"]
    for ids in resolve(resolver, group, owner.spatial_scope):
        output.update(source._morph_alpha_for_subgroup(ids, owner, progress, duration, positions))
    return output


def _union(alpha, from_field, to_field):
    ids = set(alpha.keys())
    if from_field is not None:
        ids |= set(from_field.keys())
    if to_field is not None:
        ids |= set(to_field.keys())
    return ids


def _default(native, callback, fid, channel):
    try:
        value = callback(str(fid), str(channel))
        if type(value) not in (bool, int, float, str, bytes, type(None)):
            native.compat("unsupported")
            return float(value)
        return value
    except Exception:
        return 0.


def _blend(native, rows, flags=0, values=None):
    options = dict(n=len(rows), a=[row[0] for row in rows],
                   b=[row[1] for row in rows], c=[row[2] for row in rows], flags=flags)
    if values is not None:
        options["values"] = values
    result = native.call("blend", scalar(2), options)
    return result.data[:len(rows)].tolist()


def _callback_cells(native, payload):
    source, callback, rows, from_field, to_field = payload
    output = source
    flags = 8 if from_field is None else 16
    for fid, channel, alpha in rows:
        from_value = 0. if from_field is None else from_field.get(fid, 0.)
        to_value = 0. if to_field is None else to_field.get(fid, 0.)
        raw = _default(native, callback, fid, channel)
        byte = _blend(native, [(from_value, to_value, alpha)], flags, [raw])[0]
        output[fid][channel] = byte


def _dense_cells(native, payload):
    output, rows, from_field, to_field = payload
    values = [(0. if from_field is None else from_field.get(fid, 0.),
               0. if to_field is None else to_field.get(fid, 0.), alpha)
              for fid, _channel, alpha in rows]
    data = _blend(native, values)
    for (fid, channel, _alpha), byte in zip(rows, data):
        output[fid][channel] = byte


def _morph_group(native, source, payload):
    window, group, channels, resolver, positions, progress, duration, callback, output = payload
    alpha = _alphas(source, (window, group, resolver, progress, duration, positions))
    from_field, to_field = _fields(source, window, group, resolver)
    rows = []
    for fid in _union(alpha, from_field, to_field):
        output.setdefault(str(fid), {})
        rows.extend((fid, channel, alpha.get(fid, progress)) for channel in channels)
    if callback is not None and (from_field is None or to_field is None):
        _callback_cells(native, (output, callback, rows, from_field, to_field))
    else:
        _dense_cells(native, (output, rows, from_field, to_field))


def shape(native, source, arguments):
    time, resolver = arguments["t"], arguments["resolve_group_segments"]
    duration, effective = shape_time(native, source, time)
    window, span, progress = select_window(native, source.shape_boundary_windows(), effective)
    if window is None:
        return _static(native, source, effective, duration, resolver)
    output = {}
    for group, channels in targets(source):
        payload = (window, group, channels, resolver, arguments["positions"], progress,
                   span, arguments["resolve_default"], output)
        _morph_group(native, source, payload)
    return output
