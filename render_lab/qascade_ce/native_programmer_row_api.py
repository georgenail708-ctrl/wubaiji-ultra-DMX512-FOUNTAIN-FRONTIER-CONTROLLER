"""One actual bound API/report callback; no installed instance or domain state."""
import ctypes as ct

from .native_binding import bind_native
from .native_programmer_row_types import Report, Request
from .native_programmer_row_values import decode
from .native_programmer_row_payload import prepare
from .native_programmer_row_result import semantic_error


class RowResult:
    def __init__(self, prepared, report):
        self.prepared, self.report = prepared, report

    def values(self):
        return [decode(value, self.prepared)
                for value in self.prepared.value_out[:self.report.written_values]]

    def numbers(self):
        return list(self.prepared.number_out[:self.report.written_numbers])

    def indices(self):
        return list(self.prepared.index_out[:self.report.written_indices])

    def raise_semantic(self):
        if self.report.semantic:
            raise semantic_error(self.report, self.prepared)


class RowNative:
    def __init__(self, engine, observer, function=None):
        self.engine, self.observer = engine, observer
        self.record = observer.record_row
        self.function = function
        if self.function is None:
            self.function = bind_native(engine.dll, "ce_programmer_row_eval",
                                        [ct.POINTER(Request), ct.POINTER(Report)], ct.c_int)
        if self.function is None:
            raise RuntimeError("native Programmer row ABI is unavailable")

    def run(self, payload):
        prepared, report = prepare(payload), Report()
        result = self.function(ct.byref(prepared.request), ct.byref(report))
        self.record(payload["op"], result, report)
        if result < 0:
            raise RuntimeError(f"native Programmer row system status {result}")
        return RowResult(prepared, report)
