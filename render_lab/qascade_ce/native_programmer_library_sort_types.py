"""Append-only typed sort API; no independent global domain owner."""
import ctypes as ct

from .native_curve_scalar_types import Report as ScalarReport
from .native_curve_scalar_types import Request as ScalarRequest
from .native_curve_scalar_types import Value
from .native_trajectory_types import Result

F = ct.POINTER(ct.c_double)
U = ct.POINTER(ct.c_uint64)
I = ct.POINTER(ct.c_int64)


class Request(ct.Structure):
    _fields_ = [("abi", ct.c_uint32), ("struct_size", ct.c_uint32),
                ("direction", ct.c_int32), ("flags", ct.c_uint32), ("n", ct.c_int64),
                ("scalar", ct.POINTER(ScalarRequest)), ("xs", F), ("ys", F),
                ("coordinates", ct.POINTER(Value)), ("x_tokens", U), ("y_tokens", U),
                ("indices", I)]


class Report(ct.Structure):
    _fields_ = [("status", ct.c_int32), ("semantic", ct.c_int32), ("index", ct.c_int64),
                ("stage", ct.c_int32), ("reserved", ct.c_int32), ("result", Result),
                ("scalar", ScalarReport), *((name, ct.c_uint64) for name in
                                          ("keys", "comparisons", "runs", "merges", "values"))]
