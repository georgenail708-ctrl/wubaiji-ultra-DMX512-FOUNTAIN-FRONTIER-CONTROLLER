"""Pure-data continuation and independently owned previous complete frame stores.

Only original temporal exits are copied, never QObject/compiled caches/native
pointers. Reuse TimelineFrameStore's bounded DiskCache storage and ownership.
Reference: R191 audit-incremental; DiskCache 5.6.3 Apache-2.0.
"""
from __future__ import annotations

from collections import OrderedDict
import pickle

import numpy as np

from .timeline_bake_inputs import check_cancel
from .timeline_incremental_inputs import dirty_start, fingerprint_inputs
from .timeline_frame_store import TimelineFrameStore

_STATE_FIELDS = (
    "_position", "_last_render_time", "_hold_boundary_latch", "_hold_consumed_masks",
    "_takeover_consumed_masks", "_dirty_render_item_memo", "_dirty_render_uni_memo",
    "_last_record_source_outputs_by_universe", "_last_output_universes_by_layer",
    "_last_output_universes", "_ltp_takeover_pending", "_ltp_takeover_layer",
    "_ltp_takeover_scope", "_clip_output_filter_state",
    "_light_tracking_state", "_light_tracking_time", "_light_tracking_color_cache",
    "_transition_anchor_causal_state",
)
_CHECKPOINT_BYTES = 32 * 1024 * 1024
_CHECKPOINT_COUNT = 96


def copy_tree(value):
    """Reject pointers; arrays retain exact dtype/shape/bits with immutable backing."""
    if isinstance(value, np.ndarray):
        return np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)
    if isinstance(value, dict):
        return {copy_tree(key): copy_tree(item) for key, item in value.items()}
    containers = {list: list, tuple: tuple, set: set, frozenset: frozenset}
    factory = containers.get(type(value))
    if factory is not None:
        return factory(copy_tree(item) for item in value)
    if value is None or isinstance(value, (str, bytes, bool, int, float, np.generic)):
        return value
    raise TypeError(f"unsupported temporal checkpoint value: {type(value).__name__}")


def capture_exit(engine):
    state = {name: copy_tree(getattr(engine, name)) for name in _STATE_FIELDS if hasattr(engine, name)}
    return {"state": state, "proxy_layers": copy_tree(engine._nm._layers),
            "rule_history": _capture_rule_history()}


def _capture_rule_history():
    from .audio_mod_draw_dsp import _RULE_HISTORY
    return copy_tree(_RULE_HISTORY.get())


def _restore_state(engine, state, history):
    from .transition_anchor_causality import restore_anchor_state
    restore_anchor_state(engine, state.get("_transition_anchor_causal_state"))
    for name, value in state.items():
        if name != "_transition_anchor_causal_state":
            setattr(engine, name, mutable_tree(value))
    # Existing native LT slots can be nonempty but contain a later frame's
    # values. Reconstruct them from this owned temporal exit on the next call.
    engine._lt_c_state = None
    from .audio_mod_draw_dsp import _RULE_HISTORY
    _RULE_HISTORY.set(mutable_tree(history))
    engine._active_sort_sig = engine._active_sort_cache = engine._active_priority_cache = None


def capture_range_state(engine):
    if getattr(engine, "_timeline_incremental_inputs", None) is None:
        return None
    return {"state": {name: copy_tree(getattr(engine, name)) for name in _STATE_FIELDS if hasattr(engine, name)},
            "rule_history": _capture_rule_history()}


def restore_range_state(engine, state):
    if state is not None:
        _restore_state(engine, state["state"], state["rule_history"])


def restore_exit(engine, point):
    # Runtime mutates masks in place; restore fresh writable arrays recursively.
    _restore_state(engine, point["state"], point["rule_history"])
    engine._nm._layers = mutable_tree(point["proxy_layers"])


def mutable_tree(value):
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, dict):
        return {key: mutable_tree(item) for key, item in value.items()}
    containers = {list: list, tuple: tuple, set: set, frozenset: frozenset}
    factory = containers.get(type(value))
    return factory(mutable_tree(item) for item in value) if factory is not None else value


def prepare_owned_incremental(live, worker, inputs, opts):
    worker._timeline_incremental_inputs = fingerprint_inputs(inputs, opts)
    from .timeline_ready_range import choose_previous
    worker._timeline_incremental_previous = choose_previous(live, worker._timeline_incremental_inputs)
    worker._timeline_incremental_opts = opts


def publish_complete(live, worker):
    # Caller holds the original freshness/publication guard, after a complete bake.
    live._timeline_incremental_complete = getattr(worker, "_timeline_incremental_candidate", None)


def detach_previous_stores(engine):
    previous = getattr(engine, "_timeline_incremental_complete", None)
    if previous is None:
        return
    for field, key in (("_timeline_render_frame_cache", "frames"),
                       ("_timeline_render_record_source_frame_cache", "records")):
        if getattr(engine, field) is previous[key]:
            setattr(engine, field, TimelineFrameStore())


def invalidate_stores(engine):
    detach_previous_stores(engine)
    engine._timeline_render_frame_cache.clear()
    engine._timeline_render_record_source_frame_cache.clear()


def choose_point(previous, current, interval, count):
    dirty, reason = dirty_start(previous, current)
    if previous is None or dirty <= 0.:
        return None, reason
    if previous["interval"] != interval:
        return None, "interval_changed"
    candidates = [idx for idx in previous["points"]
                  if idx + 1 <= count and (idx + 1) * interval <= dirty]
    return (max(candidates), reason) if candidates else (None, "no_prior_checkpoint")


def copy_prefix(previous, resume, frames, records, opts):
    for index in range(resume):
        if index % 128 == 0:
            check_cancel(opts)
        frames[index] = previous["frames"][index]
        records[index] = previous["records"][index]


class Checkpoints:
    """Bounded temporal exits covering the whole rendered timeline."""

    def __init__(self):
        self.points = OrderedDict()
        self.bytes = 0

    def add(self, index, point):
        size = len(pickle.dumps(point, protocol=5))
        if size > _CHECKPOINT_BYTES:
            return
        old = self.points.get(index)
        if old is not None:
            self.bytes -= old[1]
        self.points[index] = (point, size)
        self.bytes += size
        self._trim()

    def _trim(self):
        while len(self.points) > 1 and (self.bytes > _CHECKPOINT_BYTES or len(self.points) > _CHECKPOINT_COUNT):
            keys = tuple(self.points)
            # Remove the least useful interior exit: its neighbors still span
            # the shortest interval. This retains early-show coverage under the
            # original byte/count caps instead of keeping only the latest exits.
            key = (min(range(1, len(keys) - 1),
                       key=lambda pos: keys[pos + 1] - keys[pos - 1])
                   if len(keys) > 2 else 1)
            key = keys[key]
            _point, size = self.points.pop(key)
            self.bytes -= size

    def export(self):
        return {index: value[0] for index, value in self.points.items()}


def restore_live_exit(engine, point):
    """A legal live exit, independently derived from the closed causal journal.

    This is separate from original future-prepass CE checkpoint restoration;
    author NM/layers, cursor, pending LTP and Draw dictionary owner remain live.
    """
    from .temporal_journal import restore_complete_domains
    from .timeline_temporal_exit import expand_flat
    restore_complete_domains(engine, expand_flat(point))
