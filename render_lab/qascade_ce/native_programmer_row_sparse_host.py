"""Universe sparse prefix writes; no version bump or eager cache publication."""
from .native_programmer_row_payload import make


def _commit(host, layer, universe, addresses, result):
    full = host._layer_sparse_cache[layer]
    dirty = host._dirty_layer_sparse[layer]
    for address, value in zip(addresses, result.values()):
        previous = full.setdefault(universe, {})
        if previous.get(address) != value:
            previous[address] = value
            dirty.setdefault(universe, {})[address] = value
    result.raise_semantic()


def _flush(context, host, layer, universe, rows):
    if not rows:
        return
    addresses, values = zip(*rows)
    items = [dict(value_offset=index, value_count=1) for index in range(len(values))]
    payload = make("SPARSE_UNIVERSE", values, items, dict(parse_policy="int"))
    result = context.native.run(payload)
    _commit(host, layer, universe, addresses, result)


def sparse_universes(context, host, layer, values):
    host._layer_sparse_cache.setdefault(layer, {})
    host._dirty_layer_sparse.setdefault(layer, {})
    for universe, channels in (values or {}).items():
        universe, rows = int(universe), []
        for address, value in (channels or {}).items():
            try:
                address = int(address)
            except (TypeError, ValueError, OverflowError):
                _flush(context, host, layer, universe, rows)
                raise
            if 0 <= address < 512:
                rows.append((address, value))
        _flush(context, host, layer, universe, rows)
