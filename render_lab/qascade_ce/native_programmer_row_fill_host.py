"""Ordered fill metadata; native C evaluates exact maxima and leftfold mean."""
from .native_programmer_row_payload import make
from .native_programmer_row_qualification import parsed

COLORS = ("R", "G", "B", "W")


def _fixture_cache(host, fixture):
    cache = host._addr_cache.get(str(fixture))
    return host._addr_cache.get(fixture) if cache is None else cache


def _program(context, host, uni_map, effects):
    values, keys, offsets = [], {}, []

    def cell(universe, address):
        key = (int(universe), int(address))
        if key not in keys:
            keys[key] = len(values)
            values.append(uni_map.get(key[0], {}).get(key[1]))
        return keys[key], key

    complete, indices = 0, []
    try:
        for effect in effects:
            indices = []
            cache = _fixture_cache(host, effect)
            if cache:
                for color in COLORS:
                    for universe, address in cache.get(color, []) or []:
                        indices.append(cell(universe, address)[0])
            offsets.extend((len(indices), *indices))
            complete += 1
            indices = []
    except Exception:
        offsets.extend((len(indices), *indices, 0))
        _value(context, values, offsets, complete + 1, prefix=True)
        raise
    # This public caller commits one fill before evaluating the next one.  Its
    # destination lookup occurs only after C reports an active group, so the
    # scratch destination program is empty here.
    offsets.append(0)
    return values, offsets


def _destinations(host, fixture):
    cache = _fixture_cache(host, fixture)
    if cache:
        for color in COLORS:
            for universe, address in cache.get(color, []) or []:
                universe, address = int(universe), int(address)
                if 0 <= address < 512:
                    yield universe, address


def _foreign_value(context, values, offsets, count, prefix=False):
    context.statistics.compatibility("unsupported")
    total, any_light, cursor = 0., False, 0
    for _group in range(count):
        length = offsets[cursor]
        cursor += 1
        maximum = 0
        for index in offsets[cursor:cursor + length]:
            value = values[index]
            if value is not None:
                maximum = max(maximum, int(value))
                if int(value) > 0:
                    any_light = True
        cursor += length
        if prefix and _group == count - 1:
            return None
        total += maximum / 255.
    return max(0, min(255, int(round(total / count * 255.)))) if any_light else None


def _value(context, values, offsets, count, prefix=False):
    if not all(parsed(value) for value in values):
        return _foreign_value(context, values, offsets, count, prefix=prefix)
    item = dict(value_offset=0, value_count=0, index=0, total=count, flags=int(prefix))
    result = context.native.run(make("FILL", values, [item], dict(offsets=offsets, parse_policy="int")))
    result.raise_semantic()
    if prefix:
        return None
    return result.values()[0] if result.indices()[0] else None


def fill(context, host, layer, fixture_values):
    if not fixture_values or str(layer) not in ("fader", "matrix"):
        return
    try:
        fill_map, reverse = host._fill_map_current()
    except Exception:
        return
    if not fill_map or not reverse:
        return
    touched = set()
    for fixture in fixture_values:
        for fill_fixture in reverse.get(str(fixture), ()):
            touched.add(str(fill_fixture))
    if not touched:
        return
    uni_map = host._layer_sparse_cache.setdefault(layer, {})
    dirty = host._dirty_layer_sparse.setdefault(layer, {})
    for fixture in touched:
        effects = fill_map.get(fixture, [])
        if not effects:
            continue
        values, offsets = _program(context, host, uni_map, effects)
        value = _value(context, values, offsets, len(effects))
        if value is None:
            continue
        for universe, address in _destinations(host, fixture):
            if uni_map.get(universe, {}).get(address) != value:
                uni_map.setdefault(universe, {})[address] = value
                dirty.setdefault(universe, {})[address] = value
