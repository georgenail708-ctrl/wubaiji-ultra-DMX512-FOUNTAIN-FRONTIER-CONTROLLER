"""Source-only fixed declarations; no loader, dispatch, input or grid math."""
import ctypes as ct


class Atom(ct.Structure):
    _fields_ = [('kind',ct.c_uint32),('reserved',ct.c_uint32),('bits',ct.c_uint64)]


class Request(ct.Structure):
    _fields_ = [('abi',ct.c_uint32),('size',ct.c_uint32),('rows',ct.c_void_p),
        ('count',ct.c_uint64),('row_bytes',ct.c_uint64),('stride',ct.c_uint32),
        ('start_offset',ct.c_uint32),('duration_offset',ct.c_uint32),
        ('timer_ms',ct.c_int32),('timer_present',ct.c_uint32),('reserved',ct.c_uint32)]


class Grid(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in ('abi','size','status','reserved')] + [
        ('count',ct.c_uint64),('end',ct.c_double),('interval',ct.c_double)]


class CompileState(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in ('abi','size','status','reserved')] + [
        ('next',ct.c_uint64),('completed_units',ct.c_uint64),('end',ct.c_double),('request',Request)]
