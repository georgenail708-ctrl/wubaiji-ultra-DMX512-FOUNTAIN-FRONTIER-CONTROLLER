"""Fixed CE filter-preparation metadata ABI; no arithmetic or published state."""
import ctypes as ct
from dataclasses import dataclass

D = ct.POINTER(ct.c_double)
I = ct.POINTER(ct.c_int)
U = ct.POINTER(ct.c_uint8)


class Report(ct.Structure):
    _fields_ = [(name, ct.c_int) for name in
                ('abi', 'struct_size', 'semantic', 'semantic_selector', 'warning_axis')]
    _fields_ += [(name, ct.c_uint64) for name in
                 ('parameters', 'span_inputs', 'span_values', 'median_values', 'default_axes')]


def report():
    return Report(abi=1, struct_size=ct.sizeof(Report))


@dataclass(frozen=True)
class Binding:
    engine: object
    parameters: object
    spans: object
    abi: object
    size: object


@dataclass
class Numbers:
    values: object
    status: object
    errors: dict


def array(kind, values):
    return (kind * len(values))(*values)
