"""Small synchronous mathematical queries; strong results live through decode."""
from .native_programmer_row_payload import make
from .native_programmer_row_qualification import parsed


def one(native, operation, values, options=None):
    item = dict(value_offset=0, value_count=len(values))
    item.update(options or {})
    payload = make(operation, values, [item])
    if operation in ("TRUNCATE", "SPARSE_UNIVERSE"):
        payload["parse_policy"] = "int"
    result = native.run(payload)
    result.raise_semantic()
    return result


def converted(native, value):
    if not parsed(value):
        native.observer.compatibility("unsupported")
        return float(value)
    return one(native, "CONVERT_FLOAT", (value,)).numbers()[0]


def quantized(native, values, mode=0):
    items = [dict(value_offset=index, value_count=1, mode=mode)
             for index in range(len(values))]
    payload = make("QUANTIZE", values, items)
    if mode == 3:
        payload["parse_policy"] = "int"
    result = native.run(payload)
    result.raise_semantic()
    return result.values(), result.numbers()


def merged(native, row, fixture_id, channel, excluded=None):
    skipped = set(excluded or ())
    values, modes, present = [], [], 0
    for index, layer in enumerate(("fader", "joystick", "audio", "matrix")):
        value = None if layer in skipped else row._layers[layer].get(fixture_id, {}).get(channel)
        values.append(value)
        mode = row._layer_modes.get(layer, "override")
        modes.append(("override", "add", "multiply", "max", "unknown").index(
            mode if mode in ("override", "add", "multiply", "max") else "unknown"))
        if value is not None:
            present |= 1 << index
    item = dict(value_offset=0, value_count=4, layers=modes, present=present)
    result = native.run(make("MIX", values, [item]))
    result.raise_semantic()
    return result.values()[0]


def mean(native, row, channel):
    values = [row._values.get(fixture.id, {}).get(channel, 0) for fixture in row._fixtures]
    if not values:
        return 0
    return one(native, "ROW_MEAN", values).values()[0]


def curve(native, arguments, truncate=False):
    raw, name, low, high, gamma, steps = arguments
    mode = ("linear", "gamma", "scurve", "invert", "step").index(
        name if name in ("gamma", "scurve", "invert", "step") else "linear")
    return one(native, "JOYSTICK_CURVE", (raw, low, high, gamma, steps),
               dict(mode=mode, flags=int(truncate))).values()[0]
