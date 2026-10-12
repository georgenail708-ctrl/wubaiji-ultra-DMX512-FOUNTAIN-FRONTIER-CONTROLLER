"""Versioned trajectory numeric ABI; no project state or Python objects in C."""
import ctypes as ct

I32, I64, U64, D, B = ct.c_int32, ct.c_int64, ct.c_uint64, ct.c_double, ct.c_uint8


class Value(ct.Structure):
    _fields_ = [("kind", I32), ("selector", I32), ("offset", I64),
                ("size", I64), ("number", D)]


class Sample(ct.Structure):
    _fields_ = [("offset", I64), ("count", I64), ("position", D)]


class Result(ct.Structure):
    _fields_ = [("kind", I32), ("selector", I32), ("semantic", I32),
                ("stage", I32), ("number", D)]


class Time(ct.Structure):
    _fields_ = [("start", D), ("duration", D), ("rate", D),
                ("source_duration", D), ("position", D),
                ("stages", I32), ("reserved", I32)]


class TimeOut(ct.Structure):
    _fields_ = [("active", I32), ("reserved", I32), ("source", D)]


class Offset(ct.Structure):
    _fields_ = [("universe", I32), ("offset", I32), ("fixture", I64)]


class Applier(ct.Structure):
    _fields_ = [("kind", I32), ("mode", I32), ("offset", I64),
                ("count", I64), ("value", Value), ("target", Value)]


class Event(ct.Structure):
    _fields_ = [("universe", I32), ("offset", I32), ("value", Value)]


class Request(ct.Structure):
    _fields_ = [("abi", ct.c_uint32), ("struct_size", ct.c_uint32),
                ("times", ct.POINTER(Time)), ("time_count", I64),
                ("time_out", ct.POINTER(TimeOut)),
                ("samples", ct.POINTER(Sample)), ("sample_count", I64),
                ("timestamps", ct.POINTER(D)), ("points", ct.POINTER(Value)),
                ("point_count", I64), ("results", ct.POINTER(Result)),
                ("appliers", ct.POINTER(Applier)), ("applier_count", I64),
                ("offsets", ct.POINTER(Offset)), ("offset_count", I64),
                ("events", ct.POINTER(Event)), ("event_count", I64),
                ("event_out", ct.POINTER(Event)), ("event_capacity", I64),
                ("integers", ct.POINTER(B)), ("integer_size", I64),
                ("bytes_out", ct.POINTER(B)), ("byte_capacity", I64),
                ("frame", ct.POINTER(B)), ("mask", ct.POINTER(B))]


WORK = ("source", "sample", "applier", "add", "merge", "values",
        "integer_subtract", "integer_conversions", "semantic_errors")


class Report(ct.Structure):
    _fields_ = [("status", I32), ("semantic", I32), ("index", I64),
                ("written_bytes", I64), ("event_count", I64)] + [
                    (name, U64) for name in WORK]


def pointer(owner, item_type):
    """The caller retains owner; the integer-address cast creates no cycle."""
    return ct.cast(ct.addressof(owner), ct.POINTER(item_type))


def request():
    return Request(abi=1, struct_size=ct.sizeof(Request))


def rows(item_type, items):
    return (item_type * len(items))(*items)
