"""Complete default/create/legacy report ABI; no scalar calculation."""
import ctypes as ct
from . import native_runner_types as old
from . import native_runner_edit_types as edit

DIRECT,V2,LEGACY,PARTIAL,PANEL = 0,1,2,3,4


class Request(ct.Structure):
    _fields_ = [('operation',ct.c_uint32),('id',ct.c_char_p),('color',ct.c_char_p),
        ('count_bytes',ct.POINTER(ct.c_ubyte)),('n_count',ct.c_size_t)]
    _fields_ += [(name,ct.c_double) for name in ('car_length','radius','base_speed','car_unit')]
    _fields_ += [('points',ct.POINTER(old.Point)),('n_points',ct.c_int),('host_bytes',ct.c_size_t),
        ('anticipated_attach',ct.c_uint32),('anticipated_points',ct.c_int)]


class Report(ct.Structure):
    _fields_ = [('edit',edit.Report),('count_selector',ct.c_uint32),('count_fits',ct.c_uint32),
        ('phase',ct.c_uint32),('host_bytes',ct.c_size_t)]
