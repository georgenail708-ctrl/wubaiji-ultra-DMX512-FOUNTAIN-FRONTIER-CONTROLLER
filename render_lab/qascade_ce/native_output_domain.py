"""Bounded CE output-domain bindings; no DLL reload or public state ownership.

Reference: current public ownership/XY semantics and shared native_binding.
Every successful counter is incremented only after the real C return code zero.
"""
import ctypes as ct

import numpy as np

from .native_binding import bind_native, borrowed_pointer
from .native_output_mask_buffer import mask_operand
from .native_output_domain_xy import XYTask
from .native_output_mask_count import SIGNATURE as MASK_COUNT_SIGNATURE, count_one, count_rows


U8 = ct.POINTER(ct.c_uint8)
I32 = ct.POINTER(ct.c_int)
F64 = ct.POINTER(ct.c_double)
LIMIT = 8192
NATIVE = None
COUNTERS = dict(mask_calls=0, mask_values=0, mask_counts_calls=0,
                mask_count_rows=0, mask_count_values=0, ownership_calls=0, ownership_rows=0,
                pump_calls=0, pump_channels=0, xy_calls=0, xy_rows=0,
                legacy_calls=0, legacy_rows=0, compact_calls=0, compact_rows=0,
                compact_groups=0, compact_identity_cells=0, custom_relation_calls=0,
                custom_relation_cells=0, restorable_calls=0, color_calls=0,
                color_sources=0, errors=0, fallbacks=0, unsupported_callbacks=0,
                unsupported_registries=0, hold_metadata_rows=0, hold_registry_queries=0,
                inferred_calls=0, inferred_values=0, inferred_compares=0)
SIGNATURES = {
    "mask_counts": MASK_COUNT_SIGNATURE,
    "mask": ("ce_domain_mask", [ct.c_int, ct.c_int,
        ct.c_void_p, ct.c_void_p, ct.c_void_p, I32]),
    "pump": ("ce_domain_pump_max", [ct.c_int, U8, ct.c_int, I32, I32]),
    "ownership": ("ce_domain_ownership", [ct.c_int, U8, F64, U8, U8, U8, U8, U8, U8, I32]),
    "xy": ("ce_domain_xy", [ct.c_int, ct.POINTER(XYTask), U8, ct.c_int, U8]),
    "legacy": ("ce_domain_legacy_scores", [ct.c_int, F64, U8, U8, F64, U8, ct.c_double, F64, U8]),
    "compact": ("ce_domain_ownership_compact", [ct.c_int, ct.c_int, U8, F64, U8, I32, I32, U8, U8, U8, U8, I32]),
    "restorable": ("ce_domain_chain_restorable", [ct.c_int, ct.c_int, I32]),
    "color": ("ce_domain_color_pick", [ct.c_int, U8, I32, U8]),
    "inferred": ("ce_domain_inferred_mask", [U8, U8, U8, U8, U8, I32, I32]),
}


def counters():
    return COUNTERS.copy()


def note_fallback():
    COUNTERS["fallbacks"] += 1


def note_unsupported_callback():
    COUNTERS["unsupported_callbacks"] += 1


def note_hold_metadata(rows, queries, unsupported=0):
    COUNTERS["hold_metadata_rows"] += rows
    COUNTERS["hold_registry_queries"] += queries
    COUNTERS["unsupported_registries"] += unsupported


def _array(value, dtype):
    return np.ascontiguousarray(value, dtype=dtype)


def _pointer(array, pointer=U8):
    return borrowed_pointer(array, pointer)


class OutputDomain:
    def __init__(self, functions):
        self.functions = functions

    def _call(self, kind, arguments):
        try:
            code = self.functions[kind](*arguments)
            if code != 0:
                raise RuntimeError(f"Output-domain {kind} rejected its input: {code}")
        except Exception:
            COUNTERS["errors"] += 1
            raise
        COUNTERS[kind + "_calls"] += 1

    def mask(self, operation, left, right=None):
        source = mask_operand(left, operation, _array)
        other = None if right is None else mask_operand(right, operation, _array)
        if other is not None and other.shape != source.shape:
            raise ValueError("Ownership operands must have the same shape")
        output, count = np.empty_like(source), ct.c_int()
        # Fresh qualified arrays own all addresses until synchronous C returns.
        self._call("mask", (int(operation), source.size, source.ctypes.data,
            None if other is None else other.ctypes.data, output.ctypes.data, ct.byref(count)))
        COUNTERS["mask_values"] += source.size
        return output.view(np.bool_), count.value

    def mask_count(self, value):
        return count_one(self, value, COUNTERS)

    def mask_counts(self, values):
        return count_rows(self, values, COUNTERS)

    def inferred(self, base, neighbors):
        source = _array(base, np.uint8)
        rows = [None if row is None else _array(row, np.uint8) for row in neighbors]
        if len(rows) != 3 or source.shape != (512,):
            raise ValueError("Inferred ownership requires one full base and three row slots")
        if any(row is not None and row.shape != (512,) for row in rows):
            raise ValueError("Inferred ownership source rows must contain 512 channels")
        output, base_count, output_count = np.empty(512, np.uint8), ct.c_int(), ct.c_int()
        pointers = [None if row is None else _pointer(row) for row in rows]
        self._call("inferred", (_pointer(source), *pointers, _pointer(output),
                                ct.byref(base_count), ct.byref(output_count)))
        COUNTERS["inferred_values"] += 512
        if rows[0] is not None:
            COUNTERS["inferred_compares"] += 512 * sum(row is not None for row in rows[1:])
        return output.view(np.bool_)

    def pump(self, source, indices):
        data, locations = _array(source, np.uint8).reshape(-1), _array(indices, np.int32)
        output = ct.c_int()
        self._call("pump", (data.size, _pointer(data), locations.size,
                             _pointer(locations, I32), ct.byref(output)))
        COUNTERS["pump_channels"] += locations.size
        return output.value

    def ownership(self, payload):
        active, scores, data, same, restorable = payload
        arrays = (_array(active, np.uint8), _array(scores, np.float64),
                  _array(data, np.uint8), _array(same, np.uint8), _array(restorable, np.uint8))
        count = len(arrays[0])
        if count > 2**31 - 1 or any(row.shape != (count, 512) for row in arrays[:3]):
            raise ValueError("Invalid owned candidate dimensions")
        if any(row.shape != (count, count) for row in arrays[3:]):
            raise ValueError("Invalid candidate relationship dimensions")
        outputs = [np.empty((count, 512), np.uint8) for _ in range(3)]
        winner = np.empty(512, np.int32)
        self._call("ownership", (count, _pointer(arrays[0]), _pointer(arrays[1], F64),
            *(_pointer(row) for row in arrays[2:]), *(_pointer(row) for row in outputs),
            _pointer(winner, I32)))
        COUNTERS["ownership_rows"] += count
        return *(row.view(np.bool_) for row in outputs), winner

    def xy(self, tasks, source, inverse):
        data = _array(source, np.uint8)
        if data.shape != (512,) or len(tasks) > 2**31 - 1:
            raise ValueError("Physical XY transform requires one full frame")
        output = np.empty_like(data)
        self._call("xy", (len(tasks), tasks, _pointer(data), int(inverse), _pointer(output)))
        COUNTERS["xy_rows"] += len(tasks)
        return output

    def ownership_compact(self, payload):
        active, scores, data, groups, identities, relations = payload
        arrays = (_array(active, np.uint8), _array(scores, np.float64), _array(data, np.uint8))
        group_ids, identity_ids = _array(groups, np.int32), _array(identities, np.int32)
        count = len(arrays[0])
        if count > 2**31 - 1 or any(row.shape != (count, 512) for row in arrays):
            raise ValueError("Invalid compact candidate dimensions")
        if group_ids.shape != (count,) or identity_ids.shape != (count,):
            raise ValueError("Invalid compact identity dimensions")
        group_count = int(group_ids.max()) + 1 if count else 0
        relation = None if relations is None else _array(relations, np.uint8)
        if relation is not None and relation.shape != (group_count, group_count):
            raise ValueError("Invalid custom restoration relation dimensions")
        outputs = [np.empty((count, 512), np.uint8) for _ in range(3)]
        winner = np.empty(512, np.int32)
        self._call("compact", (count, group_count, _pointer(arrays[0]), _pointer(arrays[1], F64),
            _pointer(arrays[2]), _pointer(group_ids, I32), _pointer(identity_ids, I32),
            None if relation is None else _pointer(relation), *(_pointer(row) for row in outputs),
            _pointer(winner, I32)))
        COUNTERS["compact_rows"] += count
        COUNTERS["compact_groups"] += group_count
        COUNTERS["compact_identity_cells"] += count * 2
        if relation is not None:
            COUNTERS["custom_relation_calls"] += 1
            COUNTERS["custom_relation_cells"] += relation.size
        return *(row.view(np.bool_) for row in outputs), winner

    def restorable(self, group_ids):
        left, right = group_ids
        output = ct.c_int()
        self._call("restorable", (int(left), int(right), ct.byref(output)))
        return bool(output.value)

    def color(self, source, indices):
        data = None if source is None else _array(source, np.uint8).reshape(-1)
        locations, output = _array(indices, np.int32), np.empty(3, np.uint8)
        if locations.shape != (4,):
            raise ValueError("Live color requires four metadata locations")
        self._call("color", (-1 if data is None else data.size,
            None if data is None else _pointer(data), _pointer(locations, I32), _pointer(output)))
        COUNTERS["color_sources"] += int(data is not None)
        return output

    def legacy(self, payload):
        scores, active, rules, ties, boosted, boost = payload
        values, masks, filters = _array(scores, np.float64), _array(active, np.uint8), _array(rules, np.uint8)
        tie_values, flags = _array(ties, np.float64), _array(boosted, np.uint8)
        count = len(values)
        if any(row.shape != (count, 512) for row in (values, masks, filters)):
            raise ValueError("Legacy candidate dimensions differ")
        if tie_values.shape != (count,) or flags.shape != (count,):
            raise ValueError("Legacy candidate scalar dimensions differ")
        output, ownership = np.empty_like(values), np.empty_like(masks)
        self._call("legacy", (count, _pointer(values, F64), _pointer(masks), _pointer(filters),
            _pointer(tie_values, F64), _pointer(flags), float(boost), _pointer(output, F64), _pointer(ownership)))
        COUNTERS["legacy_rows"] += count
        return output, ownership.view(np.bool_)


def install(engine):
    """Bind the already loaded engine DLL once, without modifying old registry."""
    global NATIVE
    dll = getattr(engine, "dll", engine)
    functions = {key: bind_native(dll, name, args, ct.c_int)
                 for key, (name, args) in SIGNATURES.items()}
    if any(function is None for function in functions.values()):
        return False
    NATIVE = OutputDomain(functions)
    from .native_output_domain_xy import install as install_xy
    install_xy()
    from .native_output_domain_inferred import install as install_inferred
    install_inferred()
    return True
