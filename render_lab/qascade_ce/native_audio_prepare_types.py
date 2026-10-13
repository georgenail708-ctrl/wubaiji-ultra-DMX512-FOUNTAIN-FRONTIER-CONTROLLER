"""Typed ABI and retained preparation buffers; metadata has no render arithmetic."""
import ctypes as ct
from dataclasses import dataclass

D = ct.POINTER(ct.c_double)
I = ct.POINTER(ct.c_int)
OP = dict(PROCESSOR=1, CLAMP=2, MAX=3, FREQ=4, FADE=5, CLIP=6, POINT=7,
          PAIR=8, ERROR=9, CLIP_SORT=10, POINT_SORT=11)
WORK = ('processor_values', 'state_values', 'clip_rows', 'point_rows', 'round_values', 'sort_rows')


class Step(ct.Structure):
    _fields_ = [('op', ct.c_int), ('input', ct.c_int * 3), ('skip_to', ct.c_int),
                ('first', ct.c_int), ('last', ct.c_int), ('auxiliary', ct.c_int),
                ('fallback', ct.c_double), ('low', ct.c_double), ('high', ct.c_double)]


class Report(ct.Structure):
    _fields_ = [(name, ct.c_int) for name in ('abi', 'struct_size', 'semantic', 'error_selector')]
    _fields_ += [(name, ct.c_uint64) for name in WORK]


class Request(ct.Structure):
    _fields_ = [('steps', ct.c_int), ('values', ct.c_int), ('step', ct.POINTER(Step)),
                ('value', D), ('status', I), ('errors', I), ('output', D),
                ('flags', I), ('order', I), ('count', I)]


@dataclass(frozen=True)
class Binding:
    engine: object
    functions: object


@dataclass(frozen=True)
class Packed:
    request: object
    buffers: object
    errors: object


@dataclass(frozen=True)
class Result:
    output: object
    flags: object
    order: object
    count: object

    def scalar(self, index):
        return self.output[index * 4]

    def row(self, index):
        return tuple(self.output[index * 4 + offset] for offset in (0, 1, 2))

    def ordered(self, group):
        first, index = group
        return tuple(self.order[first + offset] for offset in range(self.count[index]))


def array(kind, values):
    return (kind * len(values))(*values)


def report():
    return Report(abi=1, struct_size=ct.sizeof(Report))
