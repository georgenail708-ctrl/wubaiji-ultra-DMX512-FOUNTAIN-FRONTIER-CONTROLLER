"""Compact temporal exits; no QObject/native pointer retention.

Uses the original exact TimelineFrameStore type, registered in its original
writer scope. Only hold/filter/LT/Draw values are stored. No NM layer, cursor,
output presence, LTP pending/scope or dirty-render memo is serialized per frame.
Live and owned rendering share this journal; performance needs equal-work measurement.
"""
from __future__ import annotations

import pickle
import struct
import time

import numpy as np

from .timeline_frame_store import TimelineFrameStore
from .timeline_incremental_checkpoint import copy_tree, mutable_tree

DOMAINS = ("_hold_boundary_latch", "_hold_consumed_masks", "_takeover_consumed_masks",
           "_clip_output_filter_state", "_light_tracking_state", "_light_tracking_time",
           "_light_tracking_color_cache")
BLOCK_FRAMES = 64
_MISSING = object()
_PAYLOAD_KEY = 0


def _array_same(first, second):
    if first.dtype != second.dtype or first.shape != second.shape:
        return False
    return bool(np.array_equal(first.view(np.uint8).reshape(-1),
                               second.view(np.uint8).reshape(-1)))


def same_bits(first, second):
    """Includes NaN payloads/signed zero; never ndarray identity as freshness."""
    if type(first) is not type(second):
        return False
    if isinstance(first, np.ndarray):
        return _array_same(first, second)
    if isinstance(first, float):
        return struct.pack("=d", first) == struct.pack("=d", second)
    if isinstance(first, np.generic):
        return first.dtype == second.dtype and first.tobytes() == second.tobytes()
    return first == second


def _same_container(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(tree_same(first[key], second[key]) for key in first)
    if isinstance(first, (list, tuple)):
        return len(first) == len(second) and all(tree_same(a, b) for a, b in zip(first, second))
    return same_bits(first, second)


def tree_same(first, second):
    return _same_container(first, second)


def _mapping_delta(previous, current, path, operations):
    for key in previous.keys() - current.keys():
        operations.append((path + (key,), False, None))
    for key, value in current.items():
        _delta(previous.get(key, _MISSING), value, path + (key,), operations)


def _delta(previous, current, path, operations):
    if isinstance(previous, dict) and isinstance(current, dict):
        _mapping_delta(previous, current, path, operations)
        return
    if previous is _MISSING or not tree_same(previous, current):
        operations.append((path, True, copy_tree(current)))


def _put_update(root, path, present, value):
    branch = root
    for key in path:
        if _MISSING in branch:
            raise ValueError("overlapping temporal replacement paths")
        branch = branch.setdefault(key, {})
    if branch:
        raise ValueError("overlapping temporal replacement paths")
    branch[_MISSING] = (present, value)


def _apply_updates(previous, updates, counter):
    if _MISSING in updates:
        return updates[_MISSING]
    result = dict(previous)
    if counter is not None:
        counter["mapping_copies"] = counter.get("mapping_copies", 0) + 1
    for key, children in updates.items():
        present, value = _apply_updates(result.get(key, {}), children, counter)
        if present:
            result[key] = value
        else:
            result.pop(key, None)
    return True, result


def apply_operations(previous, operations, *, counter=None):
    """Group the prefix-free original delta: each affected dict copied once."""
    if not operations:
        return previous
    updates = {}
    for path, present, value in operations:
        _put_update(updates, path, present, value)
    return _apply_updates(previous, updates, counter)[1]


def _complete_anchor(entry):
    if "causal_anchor" not in entry or "delta" in entry or "causal_delta" in entry:
        raise ValueError("incomplete causal-anchor journal anchor")
    return {"temporal": entry["anchor"], "transition_anchors": entry["causal_anchor"]}


def _require_complete_delta(previous, entry):
    if "delta" not in entry or "causal_delta" not in entry or "causal_anchor" in entry:
        raise ValueError("incomplete causal-anchor journal entry")
    if previous is None or "temporal" not in previous or "transition_anchors" not in previous:
        raise ValueError("missing prior complete temporal exit")


def apply_complete_entry(previous, entry, *, expected_index, counter=None, source_token=None):
    """Pure original-math plus causal outlet; no store, engine or writer access.

    A sequential ready cursor calls this only for its certified next index.
    Both payloads come from the same actual accepted journal entry. Sparse
    anchors replace both values together; ordinary deltas update both once.
    """
    if entry.get("index") != expected_index:
        raise ValueError("noncurrent complete temporal journal entry")
    if "anchor" in entry:
        return _bind_complete_source(_complete_anchor(entry), source_token)
    _require_complete_delta(previous, entry)
    result = {"temporal": apply_operations(previous["temporal"], entry["delta"], counter=counter),
              "transition_anchors": apply_operations(previous["transition_anchors"],
                                                      entry["causal_delta"], counter=counter)}
    return _bind_complete_source(result, source_token)


def _bind_complete_source(result, token):
    from .transition_anchor_geometry import bind_source_geometry_tokens
    result["transition_anchors"] = bind_source_geometry_tokens(result["transition_anchors"], token)
    return result


def pack_payload(payload):
    # The existing store already accepts arbitrary ndarray shapes. This opaque
    # marker is never passed to lighting output/read_cached_frame as a universe.
    # Original FrameStore accounts sys.getsizeof for owning arrays. A view of
    # pickle bytes would hide its payload entity and bypass the spill budget.
    data = np.frombuffer(pickle.dumps(payload, protocol=5), dtype=np.uint8).copy()
    return {_PAYLOAD_KEY: (data, np.frombuffer(b"", dtype=bool))}


def _payload_pair(packed):
    if type(packed) is not dict or set(packed) != {_PAYLOAD_KEY}:
        raise ValueError("nonjournal opaque payload marker")
    pair = packed[_PAYLOAD_KEY]
    if type(pair) is not tuple or len(pair) != 2:
        raise ValueError("nonjournal opaque payload pair")
    return pair


def _payload_array(value, dtype):
    if type(value) is not np.ndarray or value.dtype != dtype or value.ndim != 1:
        raise ValueError("nonjournal opaque payload array")
    return value if value.flags.owndata and value.flags.c_contiguous else value.copy()


def _owned_payload_frame(packed):
    # DiskCache protocol5 may decode a previously owning array as a view. Each
    # write, including resumed prefix copies, must own the entity accounted by
    # the unchanged original FrameStore._frame_size. No borrow of the old writer.
    data, active = _payload_pair(packed)
    data = _payload_array(data, np.dtype(np.uint8))
    active = _payload_array(active, np.dtype(bool))
    if active.size:
        raise ValueError("nonjournal opaque payload active marker")
    return {_PAYLOAD_KEY: (data, active)}


def unpack_payload(frame):
    return pickle.loads(frame[_PAYLOAD_KEY][0].tobytes())


def _read_indexed_entry(store, index, error):
    entry = unpack_payload(store.read_frame(index))
    if entry["index"] != index:
        raise ValueError(error)
    return entry


def _read_temporal(record, index, copies, *, include_causal=False):
    store = record["store"]
    if store._writer_owner is not None:
        raise RuntimeError("a live temporal reader cannot borrow the owned SQL writer")
    if not -1 <= index <= record["last"]:
        raise KeyError(index)
    anchor = index // BLOCK_FRAMES * BLOCK_FRAMES if index >= 0 else -1
    base = _read_indexed_entry(store, anchor, "noncurrent temporal journal anchor")
    result = base["anchor"]
    complete = None
    if include_causal:
        if record.get("causal_anchor_schema") != 1:
            raise ValueError("temporal journal has no causal-anchor certificate")
        complete = apply_complete_entry(None, base, expected_index=anchor, counter=copies)
    for step in range(anchor + 1, index + 1):
        item = _read_indexed_entry(store, step, "noncurrent temporal journal entry")
        if include_causal:
            complete = apply_complete_entry(complete, item, expected_index=step, counter=copies)
        else:
            result = apply_operations(result, item["delta"], counter=copies)
    return (complete["temporal"], complete["transition_anchors"]) if include_causal else result


def _record_read_cost(record, started, copies):
    stats = record.get("stats")
    if type(stats) is dict:
        with record["store"]._lock:
            stats["read_seconds"] += time.perf_counter() - started
            stats["read_calls"] += 1
            stats["read_mapping_copies"] += copies["mapping_copies"]


def read_temporal(record, index):
    """Actual direct-reader cost included, even when no Journal object remains."""
    started, copies = time.perf_counter(), {"mapping_copies": 0}
    try:
        return _read_temporal(record, index, copies)
    finally:
        _record_read_cost(record, started, copies)


def read_complete_temporal(record, index):
    """Read original seven domains and the distinct causal anchors in one pass."""
    started, copies = time.perf_counter(), {"mapping_copies": 0}
    try:
        temporal, anchors = _read_temporal(record, index, copies, include_causal=True)
        return _bind_complete_source({"temporal": temporal, "transition_anchors": anchors},
                                     record.get("causal_source_token"))
    finally:
        _record_read_cost(record, started, copies)


def capture_domains(engine, *, current_latches, native_lt_state=None):
    """current_latches comes from the independently certified original clock.

    Never use the table-preparation engine latch: it may already be the last
    show frame. The current-C LT outlet writes private copied scalar dicts only.
    Array freezing/diffs happen in Journal.record, only on changed leaves.
    """
    state = {name: getattr(engine, name) for name in DOMAINS if hasattr(engine, name)}
    state["_hold_boundary_latch"] = current_latches
    if native_lt_state is not None:
        for name in DOMAINS[-3:]:
            state[name] = dict(state[name])
        native_lt_state.back_write(state["_light_tracking_state"], state["_light_tracking_time"],
                                   state["_light_tracking_color_cache"])
    from .audio_mod_draw_dsp import _RULE_HISTORY
    return {"state": state, "rule_history": _RULE_HISTORY.get(),
            "last_time": getattr(engine, "_last_render_time", None)}


class TemporalJournal:
    """Changed leaves plus sparse compact anchors; max 64 deltas per lookup."""

    def __init__(self, initial, *, memory_budget=32 * 1024 * 1024, causal=None, source_token=None):
        started = time.perf_counter()
        self.store = TimelineFrameStore(memory_budget=memory_budget)
        self.head = copy_tree(initial)
        self.causal_revision = causal[0] if causal is not None else None
        self.causal_head = copy_tree(causal[1]) if causal is not None else None
        self.last = -1
        self.source_token = source_token
        self.stats = dict(capture_seconds=0., store_seconds=0., read_seconds=0.,
                          prepare_seconds=0., initial_seconds=0., prefix_copy_seconds=0.,
                          payload_bytes=0, entries=0, writes=0, anchors=0, changed_leaves=0,
                          mapping_copies=0, read_calls=0, read_mapping_copies=0)
        payload = dict(anchor=self.head, index=-1)
        if self.causal_head is not None:
            payload["causal_anchor"] = self.causal_head
        self._write(-1, payload)
        self.stats["initial_seconds"] = time.perf_counter() - started

    def _write(self, index, payload):
        start = time.perf_counter()
        self._write_packed(index, pack_payload(payload))
        self.stats["store_seconds"] += time.perf_counter() - start

    def _write_packed(self, index, packed):
        packed = _owned_payload_frame(packed)
        self.store[index] = packed
        self.stats["payload_bytes"] += packed[_PAYLOAD_KEY][0].nbytes
        self.stats["writes"] += 1
        self.stats["entries"] = len(self.store)

    def record(self, index, current, *, causal=None):
        if index != self.last + 1:
            raise ValueError("temporal journal must preserve original accepted frame order")
        start, operations = time.perf_counter(), []
        _delta(self.head, current, (), operations)
        self.head = apply_operations(self.head, operations, counter=self.stats)
        self.stats["capture_seconds"] += time.perf_counter() - start
        self.stats["changed_leaves"] += len(operations)
        payload = dict(index=index, delta=operations)
        if index % BLOCK_FRAMES == 0:
            payload = dict(index=index, anchor=self.head)
            self.stats["anchors"] += 1
        payload.update(self._causal_fields(index, causal))
        self._write(index, payload)
        self.last = index

    def _causal_fields(self, index, causal):
        if self.causal_head is None:
            return {}
        if causal is None:
            raise ValueError("missing current causal-anchor writer state")
        started, operations = time.perf_counter(), []
        if causal[0] != self.causal_revision:
            _delta(self.causal_head, causal[1], (), operations)
            self.causal_head = apply_operations(self.causal_head, operations, counter=self.stats)
            self.causal_revision = causal[0]
        self.stats["capture_seconds"] += time.perf_counter() - started
        return ({"causal_anchor": self.causal_head} if index % BLOCK_FRAMES == 0
                else {"causal_delta": operations})

    def read_exit(self, index):
        record = self.export()
        result = read_temporal(record, index)
        for name in ("read_seconds", "read_calls", "read_mapping_copies"):
            self.stats[name] = record["stats"][name]
        return result

    def before_target(self, target):
        return self.read_exit(int(target) - 1)

    def clone_prefix(self, previous, resume, opts):
        from .timeline_bake_inputs import check_cancel
        if resume <= 0:
            return
        started = time.perf_counter()
        for index in range(-1, resume):
            if index % 128 == 0:
                check_cancel(opts)
            self._write_packed(index, previous["store"][index])
        if self.causal_head is not None:
            complete = read_complete_temporal(previous, resume - 1)
            self.head = copy_tree(complete["temporal"])
            self.causal_head = copy_tree(complete["transition_anchors"])
            self.causal_revision = None
        else:
            self.head = copy_tree(read_temporal(previous, resume - 1))
        self.last = resume - 1
        self.stats["prefix_copy_seconds"] += time.perf_counter() - started

    def export(self):
        record = dict(store=self.store, last=self.last, schema=1, block=BLOCK_FRAMES,
                      domains=DOMAINS, stats=dict(self.stats))
        if self.causal_head is not None:
            record["causal_anchor_schema"] = 1
            record["causal_source_token"] = self.source_token
        return record


def restore_domains(engine, exit_state):
    """Called under the original render lock after its rewind/cursor setup.

    Author NM/layers, current cursor/output presence and LTP pending/scope stay
    owned by the live caller. Derived memos are rebuilt, never restored from the
    offline show. Draw's actual owner dict is updated in place.
    """
    from .transition_anchor_causality import clear_anchor_state
    clear_anchor_state(engine)
    for name in DOMAINS:
        if name in exit_state["state"]:
            setattr(engine, name, mutable_tree(exit_state["state"][name]))
        elif hasattr(engine, name):
            delattr(engine, name)  # Actual absent domain; never manufacture {}.
    engine._last_render_time = exit_state["last_time"]
    engine._lt_c_state = None
    engine._dirty_render_item_memo.clear()
    engine._dirty_render_uni_memo.clear()
    engine._active_sort_sig = engine._active_sort_cache = engine._active_priority_cache = None
    from .audio_mod_draw_dsp import _RULE_HISTORY
    owner = _RULE_HISTORY.get()
    history = mutable_tree(exit_state["rule_history"])
    owner.clear()
    owner.update(history)


def restore_complete_domains(engine, complete_exit, *, source_certificate=None):
    """Caller holds original render lock after seek/rewind, before actual render."""
    if "transition_anchors" not in complete_exit:
        raise ValueError("missing causal-anchor exit")
    restore_domains(engine, complete_exit["temporal"])
    from .transition_anchor_causality import restore_anchor_state
    restore_anchor_state(engine, complete_exit["transition_anchors"],
                         source_certificate=source_certificate)
