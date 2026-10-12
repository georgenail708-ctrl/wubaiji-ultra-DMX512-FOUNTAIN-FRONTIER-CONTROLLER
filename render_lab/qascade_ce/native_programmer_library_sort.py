"""Bound inside the single root Programmer owner; default oneC batch per sort."""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer
from .native_curve_scalar_errors import raise_semantic
from .native_programmer_library_groups import DIRECTIONS
from .native_programmer_library_metadata import scalar
from .native_programmer_library_sort_metadata import fill_request, positions_rows
from .native_programmer_library_sort_types import I, Report, Request


def validate_binding(dll):
    expected = (("abi", 1), ("request_size", ct.sizeof(Request)),
                ("report_size", ct.sizeof(Report)))
    for suffix, value in expected:
        query = bind_native(dll, "ce_programmer_library_sort_" + suffix, [], ct.c_int)
        if query is None or query() != value:
            raise RuntimeError("Mounted CE has a different Programmer sort ABI: " + suffix)


class SortBinding:
    """No counters or owner cache; the central Runtime holds and rebinds this."""

    def __init__(self, library, function=None, abi_validated=False):
        self.library = library
        if not abi_validated:
            validate_binding(library.engine.dll)
        self.function = function
        if self.function is None:
            self.function = bind_native(library.engine.dll, "ce_programmer_library_sort",
                                        [ct.POINTER(Request), ct.POINTER(Report)], ct.c_int)
        if self.function is None:
            raise RuntimeError("Mounted CE is missing ce_programmer_library_sort")

    def direction(self, ids, name, positions):
        prepared = scalar(2)
        prepared.hold(ids)
        prepared.hold(positions)
        rows = positions_rows(prepared, ids, positions)
        request = Request(abi=1, struct_size=ct.sizeof(Request), direction=DIRECTIONS[name],
                          n=len(ids), scalar=ct.pointer(prepared.request))
        fill_request(prepared, request, rows)
        output = prepared.hold(np.empty(len(ids), np.int64))
        request.indices = borrowed_pointer(output, I)
        report = Report()
        rc = self.function(ct.byref(request), ct.byref(report))
        self.library.observer.record_sort(rc, report)
        if rc or report.status != rc:
            raise RuntimeError(f"Programmer sort ABI failed: {rc}/{report.status}")
        if report.semantic:
            raise_semantic(report, prepared)
        return [ids[index] for index in output]
