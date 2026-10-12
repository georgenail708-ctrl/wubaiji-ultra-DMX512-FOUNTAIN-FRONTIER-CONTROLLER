"""Append-only payload correction; frozen02 serde remains a negative reference."""
from .native_programmer_row_types import B, attach
from .native_programmer_row_values import prepare as frozen_prepare

STRIDES = {
    "QUANTIZE": (1, 1, 0), "MIX": (1, 0, 0), "RANGE": (1, 2, 0),
    "MASTER_RANGE": (0, 2, 1), "DEFAULT": (0, 1, 0), "READ": (1, 1, 0),
    "MOTOR": (0, 0, 2), "TRUNCATE": (1, 0, 0), "VIRTUAL_DIMMER": (4, 1, 0),
    "FILL": (1, 0, 1), "TICK_ADVANCE": (2, 0, 1), "TICK_FINISH": (0, 1, 1),
    "SCRUB": (0, 2, 0), "DEBOUNCE": (0, 0, 1), "ROW_MEAN": (1, 0, 0),
    "JOYSTICK_CURVE": (1, 1, 0), "SPARSE_UNIVERSE": (1, 0, 0),
    "JITTER_ADD": (0, 1, 0), "CONVERT_FLOAT": (0, 1, 0), "TICK": (2, 1, 2),
    "MOTOR_Z_FINAL": (0, 1, 1), "ROUND_TYPED": (1, 0, 0), "JITTER_BOUNDS": (0, 2, 0),
}
APPENDED = dict(MOTOR_Z_FINAL=20, ROUND_TYPED=21, JITTER_BOUNDS=22)


def make(op, values=(), items=(), extra=None):
    strides = STRIDES[op]
    payload = dict(op=op, values=values, items=items)
    payload.update(extra or {})
    payload["capacities"] = dict(zip(("value", "number", "index"),
                                     (stride * len(items) for stride in strides)))
    payload["capacities"]["byte"] = 16
    return payload


def prepare(payload):
    constructor_payload = payload
    if payload["op"] in APPENDED:
        constructor_payload = dict(payload, op="CONVERT_FLOAT")
    prepared = frozen_prepare(constructor_payload)
    prepared.payload = payload
    if payload["op"] in APPENDED:
        prepared.request.op = APPENDED[payload["op"]]
    for index, source in enumerate(payload.get("values", ())):
        descriptor = prepared.request.values[index]
        descriptor.selector = index
        if payload["op"] == "FILL" and source is None:
            descriptor.kind = 9
            prepared.errors.pop(index, None)
    # Bound actual typed output growth by input byte metadata and result extent.
    capacity = prepared.request.integer_size * 2 + prepared.request.value_capacity * 132 + 16
    prepared.bytes_out = attach(prepared, "bytes_out", B, [0] * capacity)
    prepared.request.byte_capacity = capacity
    return prepared
