"""Readonly scalar ABI v1; reuse the exact trajectory primitive layouts."""
import ctypes as ct

from .native_trajectory_types import Result, Value, pointer


class ModelPoint(ct.Structure):
    _fields_ = [("x", Value), ("y", Value), ("c1x", Value), ("c1y", Value),
                ("c2x", Value), ("c2y", Value),
                ("present", ct.c_uint32), ("reserved", ct.c_uint32)]


class FallbackPoint(ct.Structure):
    _fields_ = [("x", Value), ("y", Value)]


class Request(ct.Structure):
    _fields_ = [("abi", ct.c_uint32), ("struct_size", ct.c_uint32),
                ("mode", ct.c_int32), ("flags", ct.c_uint32),
                ("argument", Value), ("duration", Value),
                ("model_points", ct.POINTER(ModelPoint)),
                ("model_count", ct.c_int64),
                ("fallback_points", ct.POINTER(FallbackPoint)),
                ("fallback_count", ct.c_int64),
                ("integers", ct.POINTER(ct.c_uint8)),
                ("integer_size", ct.c_int64),
                ("fallback_semantic", ct.c_int32),
                ("fallback_selector", ct.c_int32)]


class Report(ct.Structure):
    _fields_ = [("status", ct.c_int32), ("semantic", ct.c_int32),
                ("index", ct.c_int64), ("stage", ct.c_int32),
                ("reserved", ct.c_int32), ("result", Result),
                ("phase", ct.c_uint64), ("model", ct.c_uint64),
                ("segment", ct.c_uint64), ("bezier", ct.c_uint64),
                ("bezier_samples", ct.c_uint64), ("values", ct.c_uint64),
                ("clamps", ct.c_uint64), ("integer_subtract", ct.c_uint64),
                ("integer_conversions", ct.c_uint64),
                ("semantic_errors", ct.c_uint64),
                ("handled_semantic", ct.c_uint64)]


class SortReport(ct.Structure):
    """Additive v2 actual fallback-sort work; the scalar v1 ABI stays intact."""
    _fields_ = [("calls", ct.c_uint64), ("keys", ct.c_uint64),
                ("comparisons", ct.c_uint64), ("runs", ct.c_uint64),
                ("merges", ct.c_uint64)]


class Prepared:
    """All raw owner identities and buffers live until the synchronous return."""

    def __init__(self, source, request):
        self.source = source
        self.request = request
        self.owners = []
        self.buffers = []
        self.errors = {}

    def hold(self, owner):
        self.buffers.append(owner)
        return owner

    def select(self, owner):
        self.owners.append(owner)
        return len(self.owners) - 1


WORK = {
    "phase": "PHASE", "model": "MODEL", "segment": "SEGMENT",
    "bezier": "BEZIER", "bezier_samples": "BEZIER_SAMPLES", "values": "VALUES",
    "clamps": "CLAMP", "integer_subtract": "INTEGER_SUBTRACT",
    "integer_conversions": "INTEGER_CONVERSIONS",
    "semantic_errors": "SEMANTIC_ERROR", "handled_semantic": "HANDLED_SEMANTIC",
}

SORT_WORK = {"calls": "SORT_CALL", "keys": "SORT_KEYS",
             "comparisons": "SORT_COMPARISONS", "runs": "SORT_RUNS",
             "merges": "SORT_MERGES"}
