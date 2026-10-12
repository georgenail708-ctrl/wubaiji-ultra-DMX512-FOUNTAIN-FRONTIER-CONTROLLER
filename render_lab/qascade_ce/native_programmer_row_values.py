"""Lossless ordinary numeric serde; parser results are deferred to C stages."""
from .native_trajectory_values import encode
from .native_programmer_row_types import B, D, I32, I64, Item, Prepared, Value, attach


def ordinary(value):
    return type(value) in (bool, int, float)


def _serialized(value, prepared, pool, policy):
    selector = prepared.select(value)
    if ordinary(value):
        return encode(value, pool, selector)
    if type(value) not in (str, bytes, type(None)):
        raise TypeError("foreign numeric input requires original compatibility")
    parser = int if policy == "int" else float
    try:
        parsed = parser(value)
    except (TypeError, ValueError, OverflowError) as error:
        prepared.errors[selector] = error
        kind = {TypeError: 4, ValueError: 5, OverflowError: 6}[type(error)]
        return Value(kind=kind, selector=selector)
    return encode(parsed, pool, selector=-1)


def _item(source):
    fields = dict(source)
    if "layers" in fields:
        fields["layers"] = (I32 * 4)(*fields["layers"])
    return Item(**fields)


def prepare(payload):
    prepared, pool = Prepared(payload), bytearray()
    raw = payload.get("values", ())
    policy = payload.get("parse_policy", "float")
    descriptors = [_serialized(value, prepared, pool, policy) for value in raw]
    attach(prepared, "values", Value, descriptors)
    prepared.request.value_count = len(descriptors)
    items = [_item(row) for row in payload.get("items", ())]
    attach(prepared, "items", Item, items)
    prepared.request.item_count = len(items)
    attach(prepared, "numbers", D, payload.get("numbers", ()))
    prepared.request.number_count = len(payload.get("numbers", ()))
    attach(prepared, "offsets", I64, payload.get("offsets", ()))
    prepared.request.offset_count = len(payload.get("offsets", ()))
    attach(prepared, "integers", B, pool)
    prepared.request.integer_size = len(pool)
    return _outputs(prepared)


def _outputs(prepared):
    capacities = prepared.payload["capacities"]
    for name, kind in (("value", Value), ("number", D), ("index", I64), ("byte", B)):
        field = "bytes_out" if name == "byte" else name + "_out"
        count = capacities[name]
        owner = attach(prepared, field, kind, [kind() for _ in range(count)])
        setattr(prepared.request, name + "_capacity", count)
        setattr(prepared, field, owner)
    return prepared


def decode(value, prepared):
    if value.kind == 10:
        return prepared.owners[value.selector]
    if value.kind == 1:
        return value.number
    if value.kind == 2:
        data = bytes(prepared.bytes_out)[value.offset:value.offset + value.size]
        return int.from_bytes(data, "little", signed=True)
    if value.kind == 9:
        return None
    raise RuntimeError("unexpected Programmer typed output kind")


def semantic_error(report, prepared):
    item = prepared.request.items[report.index]
    for value in prepared.request.values[item.value_offset:item.value_offset + item.value_count]:
        if value.selector in prepared.errors:
            return prepared.errors[value.selector]
    return {
        1: OverflowError("int too large to convert to float"),
        2: ValueError("cannot convert float NaN to integer"),
        3: OverflowError("cannot convert float infinity to integer"),
        4: ZeroDivisionError("float division by zero"),
        11: OverflowError("integer division result too large for a float"),
    }.get(report.semantic, RuntimeError("unmapped Programmer semantic error"))
