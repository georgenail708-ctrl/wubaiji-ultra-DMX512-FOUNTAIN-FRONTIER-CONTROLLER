"""Per-call strong owner tokens; builtin conversions are performed in C."""
import ctypes as ct

import numpy as np

from .native_curve_scalar_metadata import _value
from .native_curve_scalar_types import Value
from .native_programmer_library_metadata import array
from .native_programmer_library_sort_types import F, U
from .native_programmer_library_types import UnsupportedNative
from .native_trajectory_values import attach_pool


def positions_rows(prepared, ids, positions):
    rows = []
    for fid in ids:
        row = (positions or {}).get(str(fid))
        rows.append((0., 0.) if row is None else (row[0], row[1]))
    if not all(type(value) in (bool, int, float, str, bytes, type(None))
               for row in rows for value in row):
        raise UnsupportedNative("External coordinate conversion")
    return prepared.hold(rows)


def identity_columns(prepared, rows):
    tokens, owners = {}, []
    columns = [[], []]
    for row in rows:
        for column, value in zip(columns, row):
            token = 0
            if type(value) is float:
                identity = id(value)
                if identity not in tokens:
                    owners.append(value)
                    tokens[identity] = len(owners)
                token = tokens[identity]
            column.append(token)
    prepared.hold(owners)
    return [array(prepared, column, np.uint64, U) for column in columns]


def typed_coordinates(prepared, rows):
    request = prepared.request
    pool = bytearray(ct.string_at(request.integers, request.integer_size))
    values = [_value(value, pool, prepared) for row in rows for value in row]
    owner = prepared.hold((Value * len(values))(*values))
    prepared.hold(attach_pool(request, pool))
    return ct.cast(ct.addressof(owner), ct.POINTER(Value))


def fill_request(prepared, request, rows):
    request.x_tokens, request.y_tokens = identity_columns(prepared, rows)
    if all(type(value) is float for row in rows for value in row):
        request.xs = array(prepared, [row[0] for row in rows], np.float64, F)
        request.ys = array(prepared, [row[1] for row in rows], np.float64, F)
    else:
        request.flags = 1
        request.coordinates = typed_coordinates(prepared, rows)
