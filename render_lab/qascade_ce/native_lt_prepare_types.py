"""C07 four-stage typed batch and retained metadata; research candidate."""
import ctypes as ct


class Value(ct.Structure):
    _fields_ = [('kind', ct.c_uint32), ('selector', ct.c_uint32),
               ('offset', ct.c_uint64), ('length', ct.c_uint64), ('number', ct.c_double)]


class Request(ct.Structure):
    _fields_ = [('abi', ct.c_uint32), ('struct_size', ct.c_uint32),
               ('mode', ct.c_uint32), ('count', ct.c_uint32),
               ('integers', ct.POINTER(ct.c_uint8)), ('integer_size', ct.c_uint64),
               ('values', Value * 4)]


class Report(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in ('abi', 'struct_size', 'semantic', 'selector')]
    _fields_ += [(name, ct.c_uint64) for name in (
        'consumed', 'threshold_values', 'attack_values', 'release_values', 'brightness_values')]
    _fields_ += [('threshold', ct.c_int32), ('brightness', ct.c_int32),
                ('attack', ct.c_double), ('release', ct.c_double)]


class Packed:
    def __init__(self):
        self.request = Request(abi=1, struct_size=ct.sizeof(Request), mode=0, count=4)
        self.pool, self.errors, self.owner = bytearray(), {}, None


class Binding:
    def __init__(self, engine, functions):
        self.engine, self.functions = engine, functions


def report():
    return Report(abi=1, struct_size=ct.sizeof(Report))


CANONICAL = (Value, Request, Report, Packed, Binding)
MISSING = object()
LAYOUTS = tuple((kind, tuple((name, vars(kind)[name]) for name, _scalar in kind._fields_),
                tuple((name, vars(kind).get(name, MISSING)) for name in ('_pack_', '_align_', '_anonymous_')),
                kind.__bases__) for kind in (Value, Request, Report))
