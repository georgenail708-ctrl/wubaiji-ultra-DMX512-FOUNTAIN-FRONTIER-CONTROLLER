"""Native physical conversion with original sparse prefix writes/cache barriers."""
from .native_programmer_row_payload import make
from .native_programmer_row_queries import one, quantized
from .native_programmer_row_qualification import parsed
from .native_programmer_row_fill_host import fill
from .native_programmer_row_source_host import source_value

COLORS = ("R", "G", "B", "W")


def _write(host, layer, addresses, value):
    for universe, address in addresses:
        if not 0 <= int(address) < 512:
            continue
        universe, address = int(universe), int(address)
        values = host._layer_sparse_cache[layer].setdefault(universe, {})
        if values.get(address) != value:
            values[address] = value
            host._dirty_layer_sparse[layer].setdefault(universe, {})[address] = value


def _virtual(context, host, cache, value, colors=COLORS, sparse=False, commit=None):
    # float(value) precedes the source-row/cache callback in both originals.
    converted = one(context.native, "CONVERT_FLOAT", (value,)).numbers()[0]
    base = host._sample_fixture_color_values(cache)
    selected = tuple(colors)
    raw = tuple(base.get(color, 0) for color in selected)
    if not all(type(color) in (bool, int, float) for color in raw):
        context.statistics.compatibility("unsupported")
        gain = max(0., min(1., converted / 255.))
        result = {}
        for color, component in zip(selected, raw):
            result[color] = int(round(component * gain))
            if commit is not None:
                commit(color, result[color])
        return result
    canonical = tuple(color for color in COLORS if color in selected)
    if selected != canonical:
        result = {}
        for color, component in zip(selected, raw):
            item = dict(value_offset=0, value_count=5, present=1, flags=int(sparse))
            query = context.native.run(make("VIRTUAL_DIMMER", (converted, component, 0, 0, 0), [item]))
            if commit is not None and query.values():
                commit(color, query.values()[0])
            query.raise_semantic()
            result[color] = query.values()[0]
        return result
    values = (converted, *(base.get(color, 0) if color in selected else 0 for color in COLORS))
    present = sum(1 << index for index, color in enumerate(COLORS) if color in selected)
    item = dict(value_offset=0, value_count=5, present=present, flags=int(sparse))
    query = context.native.run(make("VIRTUAL_DIMMER", values, [item]))
    if commit is not None:
        for color, component in zip(COLORS, query.values()):
            if color in selected:
                commit(color, component)
    query.raise_semantic()
    return dict(zip(COLORS, query.values()))


def expand(context, host, channel, value, addresses, cache):
    panel = context.panel
    if panel._norm_channel_type(channel) == "dimmer" and not list(addresses or []):
        selected = tuple(color for color in COLORS if cache.get(color, []))
        colors = _virtual(context, host, cache, value, selected)
        return [(int(universe), int(address), colors[color]) for color in COLORS
                for universe, address in cache.get(color, []) or []]
    fine = list(cache.get(channel + "__fine", []) or [])
    if fine:
        coarse_value, fine_value = one(context.native, "MOTOR", (value,)).indices()
        return ([(int(universe), int(address), coarse_value) for universe, address in addresses or []]
                + [(int(universe), int(address), fine_value) for universe, address in fine])
    integer = one(context.native, "TRUNCATE", (value,)).values()[0]
    return [(int(universe), int(address), integer) for universe, address in addresses or []]


def precompiled(context, host, fid, channel, value, cache, addresses):
    spec = host._addr_output_cache.get(str(fid), {}).get(str(channel))
    if spec is None:
        spec = host._addr_output_cache.get(str(fid), {}).get(context.panel._norm_channel_type(channel))
    if spec is None:
        return host._expand_channel_output(channel, value, addresses, cache)
    kind = spec[0]
    if kind == "direct":
        integer = one(context.native, "TRUNCATE", (value,)).values()[0]
        return [(universe, address, integer) for universe, address in spec[1]]
    if kind == "motor_pair":
        coarse, fine = one(context.native, "MOTOR", (value,)).indices()
        return ([(universe, address, coarse) for universe, address in spec[1]]
                + [(universe, address, fine) for universe, address in spec[2]])
    if kind == "virtual_dimmer":
        colors = _virtual(context, host, cache, value, tuple(spec[1]))
        return [(universe, address, colors[color]) for color, destinations in spec[1].items()
                for universe, address in destinations]
    return host._expand_channel_output(channel, value, addresses, cache)


def _safe_rows(context, rows):
    if not rows:
        return [], []
    outputs, numbers, cursor = [], [], 0
    while cursor < len(rows):
        suffix = rows[cursor:]
        items = [dict(value_offset=index, value_count=1, mode=2) for index in range(len(suffix))]
        result = context.native.run(make("QUANTIZE", suffix, items))
        completed = result.report.completed
        outputs.extend(result.values())
        numbers.extend(result.numbers())
        cursor += completed
        if result.report.semantic:
            # Original sparse float conversion is caught per logical channel.
            outputs.append(0)
            numbers.append(0.)
            cursor += 1
        else:
            break
    return outputs, numbers


def _flush(context, host, layer, rows):
    if not rows:
        return
    pending = list(rows)
    rows.clear()
    values, _numbers = _safe_rows(context, [value for value, _addresses in pending])
    for (_raw, addresses), value in zip(pending, values):
        _write(host, layer, addresses, value)


def _virtual_sparse(context, host, layer, cache, value, destinations):
    def commit(color, component):
        mapped = quantized(context.native, (component,), mode=3)[0][0]
        _write(host, layer, destinations[color], mapped)

    _virtual(context, host, cache, value, tuple(destinations), sparse=True, commit=commit)


def sparse_fixtures(context, host, layer, fixture_values):
    panel = context.panel
    host._layer_sparse_cache.setdefault(layer, {})
    host._dirty_layer_sparse.setdefault(layer, {})
    host._layer_sparse_version += 1
    pending = []
    try:
        for fid, channels in (fixture_values or {}).items():
            cache = host._addr_cache.get(fid)
            if not cache:
                continue
            compiled = host._addr_output_cache.get(str(fid), {})
            for channel, value in (channels or {}).items():
                addresses = cache.get(channel)
                normalized = panel._norm_channel_type(channel)
                if not addresses:
                    if normalized == "dimmer":
                        addresses = cache.get("dimmer", [])
                    if not addresses and not (normalized == "dimmer" and any(cache.get(color) for color in COLORS)):
                        continue
                spec = compiled.get(str(channel)) or compiled.get(normalized)
                if spec is not None and spec[0] == "direct" and parsed(value):
                    pending.append((value, spec[1]))
                    continue
                _flush(context, host, layer, pending)
                if not parsed(value):
                    context.statistics.compatibility("unsupported")
                    try:
                        clamped = min(255., max(0., float(value)))
                    except Exception:
                        clamped = 0.
                else:
                    _bytes, floats = _safe_rows(context, [value])
                    clamped = floats[0]
                if spec is not None and spec[0] == "virtual_dimmer":
                    _virtual_sparse(context, host, layer, cache, clamped, spec[1])
                    continue
                mapped = host._precompiled_channel_output(fid, channel, clamped, cache, addresses)
                for universe, address, output in mapped:
                    if not 0 <= int(address) < 512:
                        continue
                    integer = quantized(context.native, (output,), mode=0)[0][0]
                    _write(host, layer, [(universe, address)], integer)
    finally:
        # Earlier valid channels commit before a later metadata failure.
        _flush(context, host, layer, pending)
    host._compose_fill_lights_for_writes(layer, fixture_values)


def dispatch(context, name, arguments, original):
    host = arguments["self"]
    if name == "_row_fixture_channel_value":
        return source_value(context, host, arguments["fid"], arguments["ct"])
    if name == "_compose_fill_lights_for_writes":
        return fill(context, host, arguments["layer"], arguments["fixture_values"])
    if name == "_mark_layer_sparse_from_fixture_values":
        return sparse_fixtures(context, host, arguments["layer"], arguments["fixture_values"])
    value = arguments["value"]
    if not parsed(value):
        return original()
    if name == "_expand_channel_output":
        return expand(context, host, arguments["ch_type"], value, arguments["addr_list"], arguments["fid_cache"])
    if name == "_precompiled_channel_output":
        return precompiled(context, host, arguments["fid"], arguments["ch_type"], value,
                           arguments["fid_cache"], arguments["addr_list"])
    return original()
