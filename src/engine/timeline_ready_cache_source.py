"""Local source adapter for the unchanged cached-output body.

All complete/public flags remain actual engine values. Overrides are read only
inside the original cache method; numerical output/record/LTP has one body.
"""
import sys

from .timeline_ready_playback import (prepare_ready_frame, apply_ready_exit,
                                       finish_ready_frame, reject_ready_source)
from .timeline_temporal_replay import (replay_owns_lock, claim_outer_lock,
                                       retire_outer_lock)
from .timeline_local_render_owner import (has_local_owner, borrow_local_frame,
                                         return_local_frame)


_READY_FIELDS = {
    "_timeline_render_frame_cache": "frames",
    "_timeline_render_record_source_frame_cache": "records",
    "_timeline_render_frame_cache_signature": "layout",
    "_timeline_render_runtime_signature": "runtime",
    "_timeline_render_frame_cache_interval": "interval",
    "_timeline_render_frame_cache_end": "end",
}


def prepare_cached_frame(engine, now, layer):
    engine._m2_gate_items = None  # Original cache-method first operation.
    engine._timeline_ready_served = None
    engine._timeline_full_temporal_served = None
    engine._timeline_temporal_cache_veto = False
    engine._timeline_ready_active_plan = prepare_ready_frame(engine, now, layer)


def cache_source_value(engine, name, default=None):
    if name == "_timeline_render_cache_locked" and getattr(engine, "_timeline_temporal_cache_veto", False):
        return False  # This frame's reader veto; actual full/public truth stays unchanged.
    plan = getattr(engine, "_timeline_ready_active_plan", None)
    if plan is not None:
        if name == "_timeline_render_cache_locked":
            return True  # Local reader permit only; public lock/current unchanged.
        if name == "_hold_consumed_masks":
            return False  # Verified no external delta; owned frame has original consumption.
        key = _READY_FIELDS.get(name)
        if key is not None:
            return plan["ready"][key]
    return getattr(engine, name, default)


def flush_cached_frame(engine, changed, layer):
    engine._flush_timeline_output_now(changed, layer_name=layer)
    plan = getattr(engine, "_timeline_ready_active_plan", None)
    if plan is not None:
        apply_ready_exit(engine, plan)


def render_cached_frame(engine, now, layer):
    result = engine._render_from_timeline_frame_cache(now, layer)
    if not result:
        plan = getattr(engine, "_timeline_ready_active_plan", None)
        reject_ready_source(engine, plan)
        engine._timeline_ready_active_plan = None
        from .transition_anchor_geometry import adopt_live_source_geometry
        adopt_live_source_geometry(engine)
    return result


def acquire_render_lock(engine):
    if replay_owns_lock(engine):
        return True
    if has_local_owner(engine):
        return borrow_local_frame(engine)
    if not engine._render_lock.acquire(blocking=False):
        return False
    claim_outer_lock(engine)
    return True


def release_render_lock(engine):
    if replay_owns_lock(engine):
        return
    try:
        if sys.exception() is None:
            finish_ready_frame(engine, engine._last_render_time)
        else:
            history = getattr(engine, "_timeline_ready_history", None)
            if history is not None:
                history["plan"] = None
        engine._timeline_ready_active_plan = None
    finally:
        engine._timeline_temporal_cache_veto = False
        try:
            retire_outer_lock(engine)
        finally:
            if not return_local_frame(engine):
                engine._render_lock.release()


def read_cached_pair(frames, records, index, *, reader):
    """Both original reader results precede any NM/LTP/record commit."""
    from .timeline_cache_read import MISSING_FRAME
    frame = reader(frames, index, MISSING_FRAME)
    if frame is MISSING_FRAME:
        return MISSING_FRAME
    record = reader(records, index, MISSING_FRAME)
    return MISSING_FRAME if record is MISSING_FRAME else (frame, record)
