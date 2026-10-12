"""Lossless source serialization and original-object result reconstruction."""
import ctypes as ct

from .native_trajectory_types import B, Value, pointer


def encode(value, pool, selector=0):
    if isinstance(value, bool):
        return Value(kind=3, selector=selector, number=int(value))
    if isinstance(value, int):
        data = int.to_bytes(value, (int.bit_length(value) + 8) // 8,
                            "little", signed=True)
        descriptor = Value(kind=2, selector=selector, offset=len(pool), size=len(data))
        pool.extend(data)
        return descriptor
    if isinstance(value, float):
        return Value(kind=1, selector=selector, number=value)
    return Value(kind=0, selector=selector)


def parsed(value, pool):
    if isinstance(value, (int, float)):
        return encode(value, pool)
    try:
        return encode(float(value), pool)
    except TypeError:
        return Value(kind=4)
    except ValueError:
        return Value(kind=5)


def attach_pool(payload, pool):
    owner = (B * len(pool)).from_buffer_copy(pool)
    payload.integers = pointer(owner, B)
    payload.integer_size = len(pool)
    return owner


def output_pool(payload, capacity):
    owner = (B * capacity)()
    payload.bytes_out = pointer(owner, B)
    payload.byte_capacity = capacity
    return owner


def decode_integer(value, owner):
    data = bytes(owner)[value.offset:value.offset + value.size]
    return int.from_bytes(data, "little", signed=True)


def raise_semantic(code, value=None):
    if code == 1:
        raise OverflowError("int too large to convert to float")
    if code == 2:
        raise ValueError("cannot convert float NaN to integer")
    if code == 3:
        raise OverflowError("cannot convert float infinity to integer")
    if code == 4:
        raise ZeroDivisionError("float division by zero")
    if code == 5:
        raise OverflowError(f"Python integer {value} out of bounds for uint8")
    if code == 6:
        raise TypeError("trajectory source is not convertible to float")
    if code == 7:
        raise ValueError("trajectory source is not convertible to float")
