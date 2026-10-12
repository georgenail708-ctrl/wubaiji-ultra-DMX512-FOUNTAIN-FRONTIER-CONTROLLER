"""Research candidate ABI; root owns the only installed Programmer domain."""
import ctypes as ct

from .native_trajectory_types import Value, pointer

I32, U32, I64, U64, D, B = (ct.c_int32, ct.c_uint32, ct.c_int64,
                            ct.c_uint64, ct.c_double, ct.c_uint8)


class Item(ct.Structure):
    _fields_ = [(name, I64) for name in (
        "value_offset", "value_count", "number_offset", "number_count", "index", "total")]
    _fields_ += [("mode", I32), ("flags", U32), ("present", U32),
                ("layers", I32 * 4), ("reserved", U32)]


PAIRS = (("values", Value), ("items", Item), ("offsets", I64), ("numbers", D),
         ("integers", B), ("value_out", Value), ("number_out", D),
         ("index_out", I64), ("bytes_out", B))
COUNT_NAMES = ("value_count", "item_count", "offset_count", "number_count",
               "integer_size", "value_capacity", "number_capacity",
               "index_capacity", "byte_capacity")


class Request(ct.Structure):
    _fields_ = [("abi", U32), ("struct_size", U32), ("op", I32), ("flags", U32)]
    for (name, kind), count in zip(PAIRS, COUNT_NAMES):
        _fields_ += [(name, ct.POINTER(kind)), (count, I64)]


WORK = ("read", "row", "range", "default_value", "physical", "fill", "clock",
        "curve", "mean", "integer_arithmetic", "integer_conversions", "semantic_errors")


class Report(ct.Structure):
    _fields_ = [("status", I32), ("semantic", I32), ("index", I64),
                ("stage", I32), ("reserved", U32)]
    _fields_ += [(name, I64) for name in (
        "completed", "written_values", "written_numbers", "written_indices", "written_bytes")]
    _fields_ += [(name, U64) for name in WORK]


OPS = ("QUANTIZE", "MIX", "RANGE", "MASTER_RANGE", "DEFAULT", "READ", "MOTOR",
       "TRUNCATE", "VIRTUAL_DIMMER", "FILL", "TICK_ADVANCE", "TICK_FINISH",
       "SCRUB", "DEBOUNCE", "ROW_MEAN", "JOYSTICK_CURVE", "SPARSE_UNIVERSE", "JITTER_ADD",
       "CONVERT_FLOAT", "TICK")
OP = {name: i for i, name in enumerate(OPS)}


class Prepared:
    def __init__(self, payload):
        self.payload = payload
        self.request = Request(abi=1, struct_size=ct.sizeof(Request), op=OP[payload["op"]])
        self.owners, self.buffers, self.errors = [], [], {}

    def hold(self, owner):
        self.buffers.append(owner)
        return owner

    def select(self, owner):
        self.owners.append(owner)
        return len(self.owners) - 1


def attach(prepared, name, kind, values):
    owner = prepared.hold((kind * len(values))(*values))
    setattr(prepared.request, name, pointer(owner, kind))
    return owner
