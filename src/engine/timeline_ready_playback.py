"""Actual ready serving with legal current temporal domains.

Eager exits prevent later edits/retirement leaving a
live engine with skipped history. This has real copy/read costs requiring AB.
Long missing-frame replay is explicit O(gap), never claimed constant-time.
"""
import time

from .native_frame_clock import cache_index, rewind_needed
from .temporal_journal import _delta, apply_operations, restore_complete_domains
from .temporal_ready_cursor import temporal_at
from .timeline_temporal_exit import capture_complete_current, flatten_complete
from .timeline_full_temporal import full_temporal_current
from .timeline_temporal_live_scope import default_live_scope
from .timeline_ready_range import _same_revision, _live_temporal_current
from .timeline_temporal_replay import replay_missing, replay_owns_lock
from .transition_anchor_geometry import current_source_geometry


def _baker(engine):
    ref = getattr(engine, "_timeline_auto_baker_ref", None)
    return ref() if ref is not None else None


def _ready_epoch_current(engine, ready):
    baker = _baker(engine)
    return (baker is not None and baker._engine is engine
            and _same_revision(baker, ready["generation"], ready["revision"]))


def _ready_identity_current(engine, ready):
    if type(ready) is not dict or not ready.get("certified") or not ready.get("live_seal_certified"):
        return False
    return ready["source"] is engine._pm.project and _ready_epoch_current(engine, ready)


def _partial_current(engine):
    ready = getattr(engine, "_timeline_ready_range", None)
    if not _ready_identity_current(engine, ready):
        return None
    if not _live_temporal_current(ready) or engine._timer.interval() != ready["interval_ms"]:
        return None
    if engine._timeline_render_runtime_signature_value() != ready["runtime"]:
        return None
    tracks = ready["source"].tracks or []
    return ready if engine._cached_project_clip_layout_signature(tracks) == ready["layout"] else None


def _ready_current(engine):
    ready = _partial_current(engine)
    if ready is None:
        ready = full_temporal_current(engine)
    if ready is None:
        return None
    started = time.perf_counter()
    permitted = default_live_scope(engine)
    _cost(ready, "scope_seconds", started)
    if not permitted:
        engine._timeline_temporal_cache_veto = True
        return None
    return ready


def _cost(ready, name, started):
    costs = ready.setdefault("live_cost", {})
    costs[name] = costs.get(name, 0.) + time.perf_counter() - started


def _canonical_clock(ready, index, now):
    from qascade_ce.native_source_motion_clock import range_position
    return now == range_position(index, ready["interval"], ready["end"])


def _first_live_alignment(engine, ready):
    previous = engine._last_render_time
    index = -1 if previous is None else cache_index(ready["interval"], ready["end"], previous)
    if index >= ready["stop"] or (previous is not None and not _canonical_clock(ready, index, previous)):
        return None
    point = temporal_at(ready["temporal"], None, index)
    actual = capture_complete_current(engine)
    differences = []
    _delta(point, actual, (), differences)
    return (index, point) if not differences else None


def _history(engine, ready):
    history = getattr(engine, "_timeline_ready_history", None)
    if history is not None and history["record"] is ready:
        return history
    cursor = _first_live_alignment(engine, ready)
    if cursor is None:
        return None  # No guessed baseline and no mutation of unknown live history.
    history = dict(record=ready, cursor=cursor, live=False, index=cursor[0],
                   time=engine._last_render_time, plan=None)
    engine._timeline_ready_history = history
    return history


def _external_operations(engine, history, now):
    if history["cursor"] is None or rewind_needed(now, history["time"]):
        return []
    expected = history["cursor"][1]
    current = capture_complete_current(engine)
    # PE finally owns actual wall-clock time; journal owns canonical history
    # sample time. Time is not an external editable temporal value.
    current["temporal"]["last_time"] = expected["temporal"]["last_time"]
    operations = []
    _delta(expected, current, (), operations)
    return operations


def _point_before(ready, history, index):
    target = index - 1
    if ready.get("mode") != "full" and target == ready["stop"] - 1:
        return ready["seal_point"]["python"]
    return flatten_complete(temporal_at(ready["temporal"], history["cursor"], target))


def _restore_before(engine, ready, history, index, external):
    from .timeline_incremental_checkpoint import restore_live_exit
    bounded = min(index, ready["stop"])
    point = _point_before(ready, history, bounded)
    restore_live_exit(engine, point)
    replay_missing(engine, ready, bounded, index)
    if external:
        actual = capture_complete_current(engine)
        adjusted = apply_operations(actual, external)
        restore_complete_domains(engine, adjusted)


def _forward_live(engine, ready, history, index, now, *, layer):
    if not history["live"] or layer != "clip_playback":
        return False
    if rewind_needed(now, history["time"]) or index < history["index"]:
        return False
    replay_missing(engine, ready, history["index"] + 1, index)
    return True


def _noncanonical_frame(engine, ready, history):
    engine._timeline_temporal_cache_veto = True
    if history is not None:
        history["actual_cadence"] = True
        history["plan"] = None
    costs = ready.setdefault("live_cost", {})
    costs["clock_veto_calls"] = costs.get("clock_veto_calls", 0) + 1


def _prepare_plan(engine, ready, history, index, now, *, layer, sampled=False):
    external = _external_operations(engine, history, now)
    plan = dict(ready=ready, index=index, served=False, sample_clock=sampled)
    history["plan"] = plan
    from .audio_mod_clip_processor import audio_cache_allowed
    may_serve = (layer == "clip_playback" and index < ready["stop"] and not external
                 and audio_cache_allowed(engine, True, consume=True))
    if may_serve:
        plan["point"] = temporal_at(ready["temporal"], history["cursor"], index)
    elif not _forward_live(engine, ready, history, index, now, layer=layer):
        _restore_before(engine, ready, history, index, external)
    return plan if may_serve else None


def _prepare_current(engine, ready, index, now, *, layer):
    previous = getattr(engine, "_timeline_ready_history", None)
    previous = previous if previous is not None and previous["record"] is ready else None
    from .timeline_temporal_sample_clock import sample_clock_current
    sampled = sample_clock_current(engine) and layer == "clip_playback"
    if not sampled and not _canonical_clock(ready, index, now):
        _noncanonical_frame(engine, ready, previous)
        return None  # Before any journal read, copy, restore or nominal replay.
    if previous is not None and previous.get("actual_cadence"):
        engine._timeline_temporal_cache_veto = True
        return None  # Ordinary later ticks retain actual cadence, even on-grid.
    started = time.perf_counter()
    history = _history(engine, ready)
    _cost(ready, "adoption_seconds", started)
    if history is None:
        engine._timeline_temporal_cache_veto = True
        costs = ready.setdefault("live_cost", {})
        costs["adoption_veto_calls"] = costs.get("adoption_veto_calls", 0) + 1
        return None
    return _prepare_plan(engine, ready, history, index, now, layer=layer, sampled=sampled)


def prepare_ready_frame(engine, now, layer):
    """Inside the actual acquiring thread's original render lock, after rewind."""
    if replay_owns_lock(engine) or layer not in ("clip_playback", "clip_preview"):
        return None
    started = time.perf_counter()
    ready = _ready_current(engine)
    costs = vars(engine).setdefault("_timeline_temporal_playback_cost", {})
    costs["gate_seconds"] = costs.get("gate_seconds", 0.) + time.perf_counter() - started
    costs["gate_calls"] = costs.get("gate_calls", 0) + 1
    if ready is None:
        return None
    try:
        index = cache_index(ready["interval"], ready["end"], now)
        with current_source_geometry(engine, _source_owner(ready)):
            return _prepare_current(engine, ready, index, now, layer=layer)
    finally:
        _cost(ready, "prepare_seconds", started)


def reject_ready_source(engine, plan):
    """An original cache gate/read miss must reach the original live pipeline.

    Only the still-current exact lease can restore a certified target-before
    point. If original invalidation changed the owner/epoch, keep the real live
    temporal exit and let the unchanged original new-input pipeline handle it.
    """
    if plan is None or _ready_current(engine) is not plan["ready"]:
        return
    with current_source_geometry(engine, _source_owner(plan["ready"])):
        history = _history(engine, plan["ready"])
        if history is not None:
            _restore_before(engine, plan["ready"], history, plan["index"], [])


def apply_ready_exit(engine, plan):
    """After original frame/records/output/LTP processing accepted the pair."""
    started = time.perf_counter()
    with current_source_geometry(engine, _source_owner(plan["ready"])):
        restore_complete_domains(engine, plan["point"])
    plan["served"] = True
    if plan.get("sample_clock"):
        costs = plan["ready"].setdefault("live_cost", {})
        costs["natural_sample_hits"] = costs.get("natural_sample_hits", 0) + 1
    name = "_timeline_full_temporal_served" if plan["ready"].get("mode") == "full" else "_timeline_ready_served"
    setattr(engine, name, (plan["ready"], plan["index"]))
    costs = plan["ready"].setdefault("live_cost", {})
    costs["restore_seconds"] = costs.get("restore_seconds", 0.) + time.perf_counter() - started


def finish_ready_frame(engine, now):
    if replay_owns_lock(engine):
        return
    history = getattr(engine, "_timeline_ready_history", None)
    if history is None or history["plan"] is None:
        return
    plan, ready = history["plan"], history["record"]
    history["plan"] = None
    if plan["ready"] is not ready:
        return
    index = plan["index"]
    point = plan.get("point")
    if index < ready["stop"] and point is None:
        point = temporal_at(ready["temporal"], history["cursor"], index)
    history.update(cursor=(index, point) if point is not None else None,
                   live=not plan["served"], index=index, time=now)


def _source_owner(ready):
    return ready["published"] if ready.get("mode") == "full" else ready
