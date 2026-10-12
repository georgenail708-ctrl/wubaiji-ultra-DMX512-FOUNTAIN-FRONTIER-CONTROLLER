"""Continuation hooks for the original full-table CE sequential range.

The table builder, eligible C branch and original Python branch remain the only
renderers. Capture distinct native consumed slots and Python exits faithfully.
Reference: R191 audit-incremental; original TimelineCtx stable-key slot API.
"""
from __future__ import annotations

import time

import numpy as np

from src.engine.timeline_incremental_checkpoint import (
    Checkpoints, capture_exit, choose_point, copy_prefix, copy_tree,
    mutable_tree, restore_exit, restore_range_state,
)
from src.engine.timeline_frame_store import TimelineFrameStore


def seed_consumed(engine, ctx):
    """Original K2 initial takeover seeding, with its original exception boundary."""
    consumed = {}
    try:
        for clip_key, universes in (getattr(engine, "_takeover_consumed_masks", {}) or {}).items():
            for universe, mask in universes.items():
                slot = ctx.slot_of(clip_key, universe)
                if slot is not None:
                    ctx.set_consumed(slot, mask)
                    consumed[slot] = np.asarray(mask, dtype=bool).copy()
    except Exception:
        pass
    return consumed


def _consumed_by_key(ctx, consumed):
    return {ctx._key_by_slot[slot]: copy_tree(mask) for slot, mask in consumed.items()
            if slot in ctx._key_by_slot}


def _restore_consumed(ctx, point, existing):
    for slot in existing:
        ctx.set_consumed(slot, np.zeros(512, dtype=bool))
    result = {}
    for key, mask in point.items():
        slot = ctx.slot_of(*key)
        if slot is not None:
            result[slot] = np.asarray(mask, dtype=bool).copy()
            ctx.set_consumed(slot, result[slot])
    return result


class IncrementalRange:
    """One job-local hook; only a successful complete job can publish its record."""

    def __init__(self, engine, count, interval, end, *, reporter=None):
        self.engine, self.count, self.interval, self.end = engine, count, interval, end
        self.inputs = getattr(engine, "_timeline_incremental_inputs", None)
        self.previous = getattr(engine, "_timeline_incremental_previous", None)
        self.opts = getattr(engine, "_timeline_incremental_opts", {})
        self.enabled = self.inputs is not None and not self.inputs["unsupported"]
        self.points = Checkpoints()
        self.resume, self.point = 0, None
        self.started, self.table_started = time.perf_counter(), 0.
        self.frames, self.records = TimelineFrameStore(), TimelineFrameStore()
        self.stats = {"resume_frame": 0, "reused_frames": 0, "prefix_render_calls": 0,
                      "table_frames": count, "suffix_frames": count, "prefix_copy_seconds": 0.}
        if self.inputs is not None:
            self.opts["incremental_stats"] = self.stats
        self._prepare()
        self.temporal = None
        from src.engine.temporal_journal_range import begin_temporal
        self.temporal = begin_temporal(self, reporter)

    def _prepare(self):
        if not self.enabled:
            self.stats["reason"] = (self.inputs["unsupported"] if self.inputs is not None
                                    else "no_owned_inputs")
            self.table_started = time.perf_counter()
            return
        index, reason = choose_point(self.previous, self.inputs, self.interval, self.count)
        self.stats["reason"] = reason
        if index is not None:
            from src.engine.transition_anchor_geometry import bind_prior_checkpoint_source
            self.point = bind_prior_checkpoint_source(self.engine, self.previous["points"][index])
            self.resume = index + 1
            copy_prefix(self.previous, self.resume, self.frames, self.records, self.opts)
            for old_index, point in self.previous["points"].items():
                if old_index <= index:
                    self.points.add(old_index, bind_prior_checkpoint_source(self.engine, point))
        self.stats.update(resume_frame=self.resume, reused_frames=self.resume,
                          suffix_frames=self.count - self.resume)
        self.stats["prefix_copy_seconds"] = time.perf_counter() - self.started
        self.opts["incremental_stats"] = self.stats
        self.table_started = time.perf_counter()

    def restore(self, ctx, consumed, signatures, outputs):
        self.stats["table_seconds"] = time.perf_counter() - self.table_started
        if self.point is None:
            return self.resume, consumed, signatures, outputs
        restore_exit(self.engine, self.point["python"])
        consumed = _restore_consumed(ctx, self.point["consumed"], consumed)
        return self.resume, consumed, mutable_tree(self.point["signatures"]), mutable_tree(self.point["outputs"])

    def after_frame(self, ctx, index, *, consumed, signatures, outputs, native_lt_state=None):
        if not self.enabled:
            return
        from src.engine.temporal_journal_range import accepted_temporal
        accepted_temporal(self, index, native_lt_state)
        if index % 64 and index + 1 != self.count:
            return
        try:
            python_exit = capture_exit(self.engine)
            if native_lt_state is not None:
                state = python_exit["state"]
                native_lt_state.back_write(state["_light_tracking_state"],
                                           state["_light_tracking_time"],
                                           state["_light_tracking_color_cache"])
            point = {"python": python_exit,
                     "consumed": _consumed_by_key(ctx, consumed),
                     "signatures": copy_tree(signatures), "outputs": copy_tree(outputs)}
            self.points.add(index, point)
        except (TypeError, ValueError, OverflowError):
            # An unrepresented exit is not a legal continuation point.
            self.stats["checkpoint_rejected"] = self.stats.get("checkpoint_rejected", 0) + 1

    def finish(self, fell_back):
        self.stats["range_seconds"] = time.perf_counter() - self.started
        if fell_back:
            self.engine._timeline_incremental_candidate = None
            self.stats.update(resume_frame=0, reused_frames=0, suffix_frames=self.count,
                              reason="native_full_fallback")
            return
        if not self.enabled:
            return
        from src.engine.temporal_journal_range import export_temporal
        self.engine._timeline_incremental_candidate = {
            "temporal": export_temporal(self, self.count),
            "inputs": self.inputs, "interval": self.interval, "end": self.end,
            "count": self.count, "points": self.points.export(),
            "frames": self.frames, "records": self.records,
        }


def begin_range(engine, count, interval, end, *, frames, records, reporter=None):
    session = IncrementalRange(engine, count, interval, end, reporter=reporter)
    if session.enabled:
        return session, session.frames, session.records
    return session, frames, records


def finish_range(session, fell_back, *, engine, initial_state):
    if session is not None:
        session.finish(fell_back)
    if fell_back:
        restore_range_state(engine, initial_state)
