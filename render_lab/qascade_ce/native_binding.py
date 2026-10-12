"""Optional domain bindings with their own capability and execution counters.

The legacy CEEngine registry describes attributes installed on CEEngine itself.
Domain facades own these functions, so their probes must not invent missing
attributes in that legacy registry. Both use the same immutable opaque map.
"""
import ctypes as ct

from .ce_api_map import CE_API_MAP


def bind_native(dll, name, argtypes, restype):
    function = getattr(dll, CE_API_MAP.get(name, name), None)
    if function is None:
        function = getattr(dll, name, None)
    if function is not None:
        function.argtypes = argtypes
        function.restype = restype
    return function


def borrowed_pointer(array, typed_pointer):
    """Borrow its address; caller retains the ndarray through synchronous C.

    Casting the integer address avoids data_as's self-owning c_void_p cycle.
    Pointer fields alone never replace the caller's ndarray refs or locals.
    """
    return ct.cast(array.ctypes.data, typed_pointer)
