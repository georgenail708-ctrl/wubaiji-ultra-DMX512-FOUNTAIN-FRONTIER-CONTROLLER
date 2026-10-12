"""Typed, bounded synchronous bridge for prepared K2 candidate/frame kernels.

The caller owns public state and the exact source/ownership preparation. This
module retains all borrowed buffers until each native call returns; it does not
cache clip state, render a timeline, or publish incomplete results.
"""
from __future__ import annotations

import ctypes as ct
import operator
from dataclasses import dataclass

import numpy as np

from .native_binding import bind_native, borrowed_pointer


LIMIT = 8192
MAX_COUNT = 2**31 - 1
ABI_VERSION = 1
U8 = ct.POINTER(ct.c_uint8)
F32 = ct.POINTER(ct.c_float)
F64 = ct.POINTER(ct.c_double)
I32 = ct.POINTER(ct.c_int)
POINTER_FIELDS = (
    ("raw", U8, np.uint8), ("end", U8, np.uint8),
    ("completed", U8, np.uint8), ("gains", F32, np.float32),
    ("base", F32, np.float32), ("active", U8, np.uint8),
    ("changes", F32, np.float32), ("idle", F32, np.float32),
    ("prearrival", U8, np.uint8), ("override", F32, np.float32),
    ("base_scores", F64, np.float64),
)
SCALAR_DEFAULTS = {
    "mode": 0, "ramp": 0, "changes_mode": 0, "start": 0.,
    "rate": 1., "hold_cap": float("inf"), "tie": 0., "boost": 0., "preboost": 0.,
}


class PreparedTask(ct.Structure):
    """Native alignment and field order match ce_prepared_task ABI 1."""

    _fields_ = [(name, pointer) for name, pointer, _ in POINTER_FIELDS] + [
        ("mode", ct.c_int), ("ramp", ct.c_int), ("changes_mode", ct.c_int),
        ("start", ct.c_double), ("rate", ct.c_double),
        ("hold_cap", ct.c_double), ("tie", ct.c_double),
        ("boost", ct.c_double), ("preboost", ct.c_double),
    ]


@dataclass(frozen=True)
class EvalResult:
    data_f: np.ndarray
    data: np.ndarray
    scores: np.ndarray


@dataclass(frozen=True)
class GroupResult:
    resolved_layers: list
    winner: np.ndarray
    output: tuple[np.ndarray, np.ndarray]


SIGNATURES = {
    "abi": ("ce_prepared_frame_abi_version", []),
    "size": ("ce_prepared_task_size", []),
    "float": ("ce_prepared_frame_float", [ct.c_int, ct.POINTER(PreparedTask), F32]),
    "eval": ("ce_prepared_frame_eval", [ct.c_int, ct.POINTER(PreparedTask), F32, U8, F64]),
    "resolve": ("ce_prepared_frame_resolve_mix", [
        ct.c_int, U8, U8, F32, F64, ct.c_int, I32, I32, ct.c_int,
        ct.c_double, U8, U8, I32, I32, U8, U8,
    ]),
}


def _checked_count(value):
    count = operator.index(value)
    if not 0 <= count <= MAX_COUNT:
        raise ValueError(f"prepared count outside 0..{MAX_COUNT}: {count}")
    return count


def _vector(value, dtype):
    array = np.asarray(value)
    if dtype is np.uint8 and array.dtype == np.bool_:
        array = array.view(np.uint8)
    if array.shape != (512,) or array.dtype != np.dtype(dtype):
        raise ValueError(f"prepared vector requires 512 {np.dtype(dtype)} values")
    return np.ascontiguousarray(array)


def _required_vectors(arrays, task):
    required = ["raw", "active"]
    if arrays.get("override") is None:
        required.append("gains")
        if task.mode == 1:
            required.append("base")
    required.append("changes" if task.changes_mode == 0 else "base_scores")
    if any(arrays.get(name) is None for name in required):
        raise ValueError(f"missing prepared vector in {required}")
    if (arrays.get("end") is None) != (arrays.get("completed") is None):
        raise ValueError("prepared end and completed must be provided together")


def _task_record(values):
    task, arrays = PreparedTask(), {}
    for name, default in SCALAR_DEFAULTS.items():
        value = values.get(name, default)
        if name in ("mode", "ramp", "changes_mode"):
            value = operator.index(value)
            if value not in (0, 1):
                raise ValueError(f"invalid prepared flag {name}: {value}")
        setattr(task, name, value)
    for name, pointer, dtype in POINTER_FIELDS:
        value = values.get(name)
        if value is not None:
            array = _vector(value, dtype)
            arrays[name] = array
            setattr(task, name, borrowed_pointer(array, pointer))
    _required_vectors(arrays, task)
    return task, arrays


def _task_bundle(tasks):
    count = _checked_count(len(tasks))
    bundle, retained, overrides = (PreparedTask * count)(), [], 0
    for index, values in enumerate(tasks):
        task, arrays = _task_record(values)
        bundle[index] = task
        retained.append(arrays)
        overrides += arrays.get("override") is not None and not values.get("native_transformed", False)
    return bundle, retained, overrides


def _candidate(row):
    if len(row) != 4:
        raise ValueError("prepared candidate requires data, active, anchor, scores")
    return tuple(_vector(value, dtype) for value, dtype in zip(
        row, (np.uint8, np.uint8, np.float32, np.float64)))


def _group_rows(groups):
    _checked_count(len(groups))
    rows, index_by_identity, flat, offsets = [], {}, [], [0]
    for group in groups:
        for row in group:
            if len(row) != 4:
                raise ValueError("prepared candidate requires four vectors")
            identity = tuple(id(value) for value in row)
            index = index_by_identity.get(identity)
            if index is None:
                _checked_count(len(rows) + 1)
                index = _checked_count(len(rows))
                rows.append(_candidate(row))
                index_by_identity[identity] = index
            _checked_count(len(flat) + 1)
            flat.append(index)
        offsets.append(_checked_count(len(flat)))
    return rows, np.asarray(offsets, np.int32), np.asarray(flat, np.int32)


def _row_matrices(rows):
    if not rows:
        return tuple(np.empty((0, 512), dtype) for dtype in
                     (np.uint8, np.uint8, np.float32, np.float64))
    return tuple(np.stack([row[index] for row in rows]) for index in range(4))


def _resolve_buffers(nflat, groups):
    return (
        np.empty((nflat, 512), np.uint8), np.empty((nflat, 512), np.uint8),
        np.empty(nflat, np.int32), np.empty((groups, 512), np.int32),
        np.empty((groups, 512), np.uint8), np.empty((groups, 512), np.uint8),
    )


def _group_results(rows, offsets, flat, buffers):
    effective, data_eff, counts, winner, output, active = buffers
    results = []
    for group in range(len(offsets) - 1):
        first, last = int(offsets[group]), int(offsets[group + 1])
        layers = [(data_eff[index], effective[index].view(np.bool_), rows[flat[index]][2])
                  for index in range(first, last) if counts[index] > 0]
        results.append(GroupResult(layers, winner[group],
                                   (output[group], active[group].view(np.bool_))))
    return results


def _group_fits(batch, occurrences, next_count):
    return len(batch) < LIMIT and occurrences + next_count <= LIMIT


def _group_batches(groups):
    batch, occurrences = [], 0
    for group in groups:
        count = _checked_count(len(group))
        if batch and not _group_fits(batch, occurrences, count):
            yield batch
            batch, occurrences = [], 0
        batch.append(group)
        occurrences += count
    if batch or not groups:
        yield batch


def _merge_eval(results):
    if len(results) == 1:
        return results[0]
    return EvalResult(*(np.concatenate([getattr(result, name) for result in results])
                        for name in ("data_f", "data", "scores")))


class PreparedNative:
    """Optional native binding with successful-stage and actual-call counters."""

    def __init__(self, functions):
        if functions["abi"]() != ABI_VERSION:
            raise RuntimeError("prepared native ABI version mismatch")
        if functions["size"]() != ct.sizeof(PreparedTask):
            raise RuntimeError("prepared native task layout mismatch")
        self._functions = functions
        self._counts = dict.fromkeys((
            "eval_calls", "eval_successes", "eval_tasks", "native_eval_tasks",
            "override_eval_tasks", "float_calls", "float_successes", "float_tasks", "resolve_calls", "resolve_successes",
            "resolve_groups", "resolve_candidates", "pending_task_total",
            "pending_group_total", "eval_stages_completed", "resolve_stages_completed",
        ), 0)

    @classmethod
    def from_engine(cls, engine):
        dll = getattr(engine, "dll", engine)
        functions = {key: bind_native(dll, name, args, ct.c_int)
                     for key, (name, args) in SIGNATURES.items()}
        if any(function is None for function in functions.values()):
            return None
        return cls(functions)

    def counters(self):
        return self._counts.copy()

    def float_values(self, tasks):
        """Unclamped native float stage before ordered audio and safety filters."""
        result = np.empty((len(tasks), 512), np.float32)
        for start in range(0, len(tasks), LIMIT):
            bundle, retained, _ = _task_bundle(tasks[start:start + LIMIT])
            self._counts["float_calls"] += 1
            code = self._functions["float"](len(bundle), bundle, borrowed_pointer(result[start:], F32))
            if code != 0:
                raise RuntimeError(f"prepared native float failed: {code}")
            self._counts["float_successes"] += 1
            self._counts["float_tasks"] += len(bundle)
            del retained
        return result

    def preparedeval(self, tasks):
        """Evaluate bounded batches; the full stage completes only after all succeed."""
        count = len(tasks)
        self._counts["pending_task_total"] += count
        try:
            starts = range(0, count, LIMIT) if count else (0,)
            results = [self._eval_batch(tasks[start:start + LIMIT]) for start in starts]
            merged = _merge_eval(results)
        finally:
            self._counts["pending_task_total"] -= count
        self._counts["eval_stages_completed"] += 1
        return merged

    def _eval_batch(self, tasks):
        """Evaluate borrowed descriptors; failures return no partially complete result."""
        bundle, retained, overrides = _task_bundle(tasks)
        count = len(bundle)
        result = EvalResult(np.empty((count, 512), np.float32),
                            np.empty((count, 512), np.uint8),
                            np.empty((count, 512), np.float64))
        self._counts["eval_calls"] += 1
        code = self._functions["eval"](
            count, bundle, borrowed_pointer(result.data_f, F32),
            borrowed_pointer(result.data, U8), borrowed_pointer(result.scores, F64))
        if code != 0:
            raise RuntimeError(f"prepared native evaluation failed: {code}")
        self._counts["eval_successes"] += 1
        self._counts["eval_tasks"] += count
        self._counts["native_eval_tasks"] += count - overrides
        self._counts["override_eval_tasks"] += overrides
        # Retained inputs are alive through the synchronous call and accounting.
        del retained
        return result

    def resolve_mix(self, groups, mod_gain=1.0):
        """Keep each group whole while batching total row occurrences."""
        self._counts["pending_group_total"] += len(groups)
        try:
            results = []
            for batch in _group_batches(groups):
                results.extend(self._resolve_batch(batch, mod_gain))
        finally:
            self._counts["pending_group_total"] -= len(groups)
        self._counts["resolve_stages_completed"] += 1
        return results

    def _resolve_batch(self, groups, mod_gain):
        """Resolve main/record groups in one call without repeating task evaluation."""
        rows, offsets, flat = _group_rows(groups)
        data, active, anchors, scores = _row_matrices(rows)
        buffers = _resolve_buffers(len(flat), len(groups))
        effective, data_eff, counts, winner, output, output_active = buffers
        self._counts["resolve_calls"] += 1
        code = self._functions["resolve"](
            len(rows), borrowed_pointer(data, U8), borrowed_pointer(active, U8),
            borrowed_pointer(anchors, F32), borrowed_pointer(scores, F64), len(groups),
            borrowed_pointer(offsets, I32), borrowed_pointer(flat, I32), len(flat), float(mod_gain),
            borrowed_pointer(effective, U8), borrowed_pointer(data_eff, U8), borrowed_pointer(counts, I32),
            borrowed_pointer(winner, I32), borrowed_pointer(output, U8), borrowed_pointer(output_active, U8))
        if code != 0:
            raise RuntimeError(f"prepared native resolution failed: {code}")
        self._counts["resolve_successes"] += 1
        self._counts["resolve_groups"] += len(groups)
        self._counts["resolve_candidates"] += len(flat)
        return _group_results(rows, offsets, flat, buffers)
