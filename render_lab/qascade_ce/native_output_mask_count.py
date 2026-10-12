"""Count-only buffers for the existing OutputDomain's synchronous CE call.

Reference: ce_domain_mask op0 and the frozen K05ac row-major count ABI.
There is no additional DLL, native owner, scratch cache, or retained pointer.
"""
import ctypes as ct

import numpy as np

from .native_binding import borrowed_pointer


U8 = ct.POINTER(ct.c_uint8)
I32 = ct.POINTER(ct.c_int32)
I64 = ct.POINTER(ct.c_int64)
INT32_MAX = 2**31 - 1


class MaskCountRow(ct.Structure):
    _fields_ = [("data", U8), ("length", ct.c_int32), ("reserved", ct.c_int32)]


SIGNATURE = ("ce_domain_mask_counts", [ct.c_int32, ct.POINTER(MaskCountRow), I32, I32, I64])


def _input(value):
    source = np.asarray(value, dtype=bool)
    if source.size > INT32_MAX:
        raise ValueError("Mask count length exceeds the signed int32 ABI")
    return np.ascontiguousarray(source).reshape(-1)


def _row(source):
    return MaskCountRow(borrowed_pointer(source, U8), source.size, 0)


def _rows(values):
    refs = [_input(value) for value in values]
    if len(refs) > INT32_MAX:
        raise ValueError("Mask row count exceeds the signed int32 ABI")
    rows = (MaskCountRow * len(refs))()
    for index, source in enumerate(refs):
        rows[index] = _row(source)
    return refs, rows


def _work(counters, report):
    counters["mask_count_rows"] += int(report[0])
    counters["mask_count_values"] += int(report[1])


def count_one(domain, value, counters):
    source = _input(value)
    row, count, selected = _row(source), ct.c_int32(), ct.c_int32()
    report = (ct.c_int64 * 4)()
    domain._call("mask_counts", (1, ct.byref(row), ct.byref(count),
                                ct.byref(selected), report))
    _work(counters, report)
    # Existing public count work remains in the original single-mask counters.
    counters["mask_calls"] += 1
    counters["mask_values"] += int(report[1])
    return count.value


def count_rows(domain, values, counters):
    refs, rows = _rows(values)
    count = len(refs)
    counts, selected = np.empty(count, np.int32), np.empty(count, np.int32)
    report = np.empty(4, np.int64)
    arguments = (count, rows if count else None,
                 borrowed_pointer(counts, I32) if count else None,
                 borrowed_pointer(selected, I32) if count else None,
                 borrowed_pointer(report, I64))
    # refs retains every borrowed input until _call has returned synchronously.
    domain._call("mask_counts", arguments)
    _work(counters, report)
    return counts, selected[:int(report[2])], report
