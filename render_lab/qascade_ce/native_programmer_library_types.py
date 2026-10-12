"""Research batch ABI; one dense request and one actual report owner."""
import ctypes as ct
from dataclasses import dataclass

from .native_curve_scalar_types import Report as ScalarReport
from .native_curve_scalar_types import SortReport
from .native_curve_scalar_types import Request as ScalarRequest
from .native_curve_scalar_types import Value
from .native_trajectory_types import Result

F = ct.POINTER(ct.c_double)
I = ct.POINTER(ct.c_int64)
B = ct.POINTER(ct.c_uint8)


class Request(ct.Structure):
    _fields_ = [("abi", ct.c_uint32), ("struct_size", ct.c_uint32),
                ("op", ct.c_int32), ("flags", ct.c_uint32),
                ("scalar", ct.POINTER(ScalarRequest)),
                ("n", ct.c_int64), ("group_n", ct.c_int64),
                ("a", F), ("b", F), ("c", F), ("d", F),
                ("values", ct.POINTER(Value)), ("offsets", I),
                ("groups", Value), ("batches", Value),
                ("p0", ct.c_double), ("p1", ct.c_double),
                ("p2", ct.c_double), ("p3", ct.c_double),
                ("out_f", F), ("out_i", I), ("out_b", B), ("out_flags", B),
                ("typed_a", ct.POINTER(Value)), ("typed_b", ct.POINTER(Value)),
                ("typed_p", ct.POINTER(Value)), ("out_value", ct.POINTER(Value)),
                ("out_bytes", B), ("byte_capacity", ct.c_int64)]


class Report(ct.Structure):
    _fields_ = [("status", ct.c_int32), ("semantic", ct.c_int32),
                ("index", ct.c_int64), ("stage", ct.c_int32),
                ("reserved", ct.c_int32), ("result", Result),
                ("scalar", ScalarReport),
                *((name, ct.c_uint64) for name in
                  ("values", "quantize", "segment", "direction", "reveal",
                   "shape", "window", "morph", "blend", "partition", "offsets")),
                ("written_bytes", ct.c_int64), ("scalar_sort", SortReport)]


@dataclass
class BatchResult:
    floats: object
    indices: object
    data: object
    flags: object
    report: Report
    prepared: object
    value: object = None
    integer_bytes: object = None


class UnsupportedNative(RuntimeError):
    """Original numeric route must run once before any state consumption."""


OPERATIONS = dict(quantize=1, segment=2, direction=3, reveal=4, morph=5,
                  shape=6, windows=7, blend=8, path=9, shape_time=10,
                  window_select=11, static_select=12, partition=13,
                  offsets=14, effective=15, unit_phase=16)
WORK = {key: key.upper() for key in
        ("values", "quantize", "segment", "direction", "reveal", "shape",
         "window", "morph", "blend", "partition", "offsets")}
