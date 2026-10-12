"""Bound once by the central Programmer domain; no duplicate statistics."""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer
from .native_curve_scalar_errors import raise_semantic
from .native_programmer_library_metadata import array, extend
from .native_programmer_library_types import B, F, I, BatchResult, OPERATIONS
from .native_programmer_library_types import Report, Request, UnsupportedNative
from .native_curve_scalar_types import Value


def _outputs(prepared, request, count):
    owners = (np.empty(count, np.float64), np.empty(count, np.int64),
              np.empty(count, np.uint8), np.empty(count, np.uint8))
    fields = (("out_f", F), ("out_i", I), ("out_b", B), ("out_flags", B))
    for owner, (name, pointer_type) in zip(owners, fields):
        prepared.hold(owner)
        setattr(request, name, borrowed_pointer(owner, pointer_type))
    return owners


def _inputs(prepared, request, options):
    options = dict(options)
    for name in ("a", "b", "c", "d"):
        if name in options:
            rows = options[name]
            if name in ("a", "b") and any(type(value) is not float for value in rows):
                options["typed_" + name] = rows
            else:
                setattr(request, name, array(prepared, rows, np.float64, F))
    if "offsets" in options:
        request.offsets = array(prepared, options["offsets"], np.int64, I)
    parameters = [options.get("p" + str(i), 0.) for i in range(4)]
    typed = any(type(value) is not float for value in parameters)
    if typed:
        options["typed_p"] = parameters
    for name in ("flags", "n", "group_n", "p0", "p1", "p2", "p3"):
        if name in options:
            if not typed or not name.startswith("p"):
                setattr(request, name, options[name])
    extend(prepared, options, request)


def _semantic(report, prepared):
    if not report.semantic:
        return
    if report.stage in (101, 103):
        if report.semantic == 2:
            raise ValueError("cannot convert float NaN to integer")
        raise OverflowError("cannot convert float infinity to integer")
    if report.stage == 104:
        value = prepared.owners[report.result.selector]
        raise TypeError(f"'{type(value).__name__}' object cannot be interpreted as an integer")
    raise_semantic(report, prepared)


class LibraryNative:
    """Central owner injects record(return_code, actual_report) and compat(kind)."""

    def __init__(self, engine, observer, function=None):
        self.engine, self.observer = engine, observer
        self.function = function
        if self.function is None:
            self.function = bind_native(engine.dll, "ce_programmer_library_eval",
                                        [ct.POINTER(Request), ct.POINTER(Report)], ct.c_int)
        if self.function is None:
            raise RuntimeError("Mounted CE DLL is missing ce_programmer_library_eval")

    def call(self, operation, prepared, options):
        request = Request(abi=2, struct_size=ct.sizeof(Request),
                          op=OPERATIONS[operation], scalar=ct.pointer(prepared.request))
        _inputs(prepared, request, options)
        owners = _outputs(prepared, request, max(4, 3 * (request.n + 1)))
        value = prepared.hold(Value())
        byte_count = max(32, prepared.request.integer_size * 2 + 32)
        integer_bytes = prepared.hold(np.empty(byte_count, np.uint8))
        request.out_value = ct.pointer(value)
        request.out_bytes = borrowed_pointer(integer_bytes, B)
        request.byte_capacity = byte_count
        report = Report()
        rc = self.function(ct.byref(request), ct.byref(report))
        self.observer.record(rc, report)
        if rc == -3:
            raise UnsupportedNative("Unproven nonfinite stable sort semantics")
        if rc or report.status != rc:
            raise RuntimeError(f"Programmer Library ABI failed: {rc}/{report.status}")
        _semantic(report, prepared)
        return BatchResult(*owners, report, prepared, value, integer_bytes)

    def compat(self, kind):
        self.observer.compat(kind)
