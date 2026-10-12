# Actual CPython adaptation notice — R214 scalar, typed arithmetic and sorting

Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.

The adopted CE sources described here derive from CPython **3.13.13**, fixed commit `01104ce1beb3135c2e0c01ec835b994c1f55a1c0`, under Python Software Foundation License Version 2. The complete retained license is [CPython-3.13.13-LICENSE.txt](CPython-3.13.13-LICENSE.txt), SHA256 `0c642c03bfe93432b716a3e3e2f0a77ef03e94c5f9b57a90f0ff95f206c221b5` (13810 bytes). The exactly fetched fixed Git LICENSE blob is `14603b95c2e23b5a4cd6ff93f50348d1817f7638`, SHA256 `78b12c3a81360b357002334f0e70ea0e92eebf7a9b358805c03c48484945f3bb` (13809 bytes); the retained copy differs only by one final newline. Its full terms and preexisting bytes remain unchanged. The earlier [R211 integer adaptation notice](CPython-R211-ADAPTATION-NOTICE.md) remains applicable and is not replaced.

This records actual source adoption and modifications. It does not certify a native test, performance improvement, complete CE migration or release; current build, original-output/state and equal-work project acceptance are separately recorded.

## Scalar comparison

Primary source: [Objects/floatobject.c, float_richcompare, lines409–586](https://github.com/python/cpython/blob/01104ce1beb3135c2e0c01ec835b994c1f55a1c0/Objects/floatobject.c#L409), blob `986edd597d6d1d1f58920cd0542238f8cac7cb05`. Product `render_lab/csrc/ce_curve_scalar_values.inc` adapts mixed float/arbitrary-integer comparison. NaN/Inf handling precedes finite integral/fractional decomposition; signed base256 comparisons remain exact and unordered NaN behavior is retained. Arbitrary integers are not rounded to double before comparison; private PyLong storage is not borrowed.

Base256 scratch ownership and conversion reuse the already adopted R211 integer primitives. Project coordinate, ordered clamp and remainder primitives remain in use. Scalar Model interpolation retains the original ratio-first formula, 33 Bezier samples and first inclusive interval. Explicit selectors/reports retain original endpoint objects and exception consumption order without Python numerical callbacks.

## Typed sum, multiplication and division

Primary source: [Python/bltinmodule.c, builtin_sum_impl, lines2554–2762](https://github.com/python/cpython/blob/01104ce1beb3135c2e0c01ec835b994c1f55a1c0/Python/bltinmodule.c#L2554), blob `bcf7e7355daa99ea9579857e99b1afc9a24481a5`. Product `render_lab/csrc/ce_programmer_row_sum.inc` owns `ce_pr_sum_values`, reused by Row and Library. It preserves initial exact integer accumulation, the target Windows32-bit long extraction boundary, transition to float state, float-only Neumaier compensation, plain integer additions within float state, and the non-reentrant generic tail. It does not substitute math.fsum, NumPy sum, a reordered reduction or an always-float accumulator.

`render_lab/csrc/ce_programmer_library_sort_sum.inc` is an int64 count-width adapter. Ordinary counts call the existing project `_ce_py_sum_f64`; the wide loop preserves the same compensated order. It is not a separate sum algorithm.

Primary source: [Objects/longobject.c, x_mul lines3818–3917 and long_true_divide lines4488–4746](https://github.com/python/cpython/blob/01104ce1beb3135c2e0c01ec835b994c1f55a1c0/Objects/longobject.c#L3818). Product `render_lab/csrc/ce_programmer_row_integer.inc` adapts bounded base256 multiplication and integer/positive-uint64 denominator division. Guard/sticky bits, nearest-even conversion, overflow and original operand consumption stages use explicit owned scratch and semantic reports. No PyLong private layout, Python.h, GIL-dependent API or Python numerical callback is used.

## Shared stable selector sort

Primary sources: [Objects/listobject.c](https://github.com/python/cpython/blob/01104ce1beb3135c2e0c01ec835b994c1f55a1c0/Objects/listobject.c), blob `f5acc5c1bef4ee1748816b8eeeaa4f8677cdf419`; [Objects/listsort.txt](https://github.com/python/cpython/blob/01104ce1beb3135c2e0c01ec835b994c1f55a1c0/Objects/listsort.txt), blob `f387d9c116e5028ce06b9c71de8c8f6160c94a3c`; [Objects/tupleobject.c](https://github.com/python/cpython/blob/01104ce1beb3135c2e0c01ec835b994c1f55a1c0/Objects/tupleobject.c), blob `818814b663d53451b2931b266c1864494c6b4ce5`.

Product `render_lab/csrc/ce_programmer_library_sort_{core_types,runs,gallop,memory,merge_lo,merge_hi,schedule,driver}.inc` directly adapts fixed stable Powersort: run detection, binary insertion, galloping, merges and scheduling retain the upstream comparison order. Readonly int64 selectors replace PyObject storage; reference counting/allocation becomes explicit owned scratch, malloc/free and error reports; prefixed symbols and typed compare/context avoid CPython private ABI. Row selector zero is valid, so a pointer-validity assertion replaces a mistaken row-value truth test; original source/candidate artifacts remain preserved separately.

`ce_programmer_library_sort_compare.inc` retains tuple lexicographic equality and shared-owner behavior. `ce_curve_scalar_sort.inc` calls this same selector kernel with exact scalar-key `<`, omitting tuple owner-equality shortcuts. Fallback sorts only when actually consumed and containing at least two rows; successful Model results and earlier duration exceptions do not sort unused input. Arrays remain readonly, selectors retain original ownership, and reports count actual key preparation, comparisons, runs and merges.

Exact frozen source/extraction and actual changes are in the R211/R214 ledgers. Blender, Kdenlive and MLT cache/editing behavior informed design only; no GPL/LGPL implementation from them was copied into these CE sources.
