"""Research range hooks; original CE math/output/checkpoint ordering unchanged.

Source qualification is the same default reporter and timing guard. A partial
lease alone is not a playback permit: the full journal coverage and independent
live-history/time/output contract must pass before a live adapter is mounted.
"""
import time

from .temporal_journal import TemporalJournal, capture_domains
from .temporal_latch_clock import LatchTimeline


def _origin_reporter_current(callback):
    if callback is None:
        return True
    from importlib import import_module
    from .timeline_owned_reporter import _RAW
    try:
        guard = import_module("qascade_ce.prepared_prepass_guard")
    except ModuleNotFoundError:
        return False
    return (guard.canonical(callback) and callback.__code__.co_qualname == _RAW
            and guard._baker_current(guard._cells(callback).get("self")))


def capture_owned_origin(engine, opts):
    """Within original owned Draw scope, after seek0 and before precompile.

    Frozen pure values only. No clear/reset of engine math, no native pointer,
    no live author/NM mutation. Foreign callbacks retain the original route.
    """
    if not getattr(engine, "_timeline_bake_owned", False):
        return
    engine._timeline_temporal_origin = None
    if not _origin_reporter_current(opts.get("progress_callback")):
        return
    from .timeline_incremental_checkpoint import copy_tree
    started = time.perf_counter()
    try:
        origin = copy_tree(capture_domains(engine, current_latches=engine._hold_boundary_latch))
    except (TypeError, ValueError, OverflowError):
        return
    from .transition_anchor_causality import capture_anchor_state
    causal = capture_anchor_state(engine)
    causal = (causal[0], copy_tree(causal[1]))
    engine._timeline_temporal_origin = (origin, time.perf_counter() - started, causal)


def _prior_journal(session):
    previous = session.previous
    if type(previous) is not dict:
        return None
    record = previous.get("temporal")
    if type(record) is not dict:
        return None
    if record.get("schema") != 1 or record.get("last", -1) < session.resume - 1:
        return None
    return record


def _reuse_prefix(session, journal):
    if session.resume == 0:
        return True
    previous = _prior_journal(session)
    if previous is None:
        return False
    journal.clone_prefix(previous, session.resume, session.opts)
    return True


def begin_temporal(session, reporter):
    if not session.enabled:
        return None
    from .timeline_owned_reporter import default_owned_progress
    package = type(session).__module__.rsplit(".", 1)[0]
    if not default_owned_progress(reporter, package):
        return None
    engine = session.engine
    origin = getattr(engine, "_timeline_temporal_origin", None)
    if origin is None:
        session.stats["temporal_veto"] = "owned_zero_epoch_not_captured"
        return None
    started = time.perf_counter()
    clock = LatchTimeline.from_owned_zero(engine, engine._pm.project.tracks or [],
        count=session.count, interval=session.interval, end=session.end, opts=session.opts)
    if clock is None:
        return None
    prepared = time.perf_counter() - started
    from .transition_anchor_geometry import SOURCE
    source = getattr(engine, SOURCE, {})
    journal = TemporalJournal(origin[0], causal=origin[2], source_token=source.get("token"))
    journal.stats["origin_seconds"] = origin[1]
    journal.stats["prepare_seconds"] = prepared
    if not _reuse_prefix(session, journal):
        journal.store.clear()
        session.stats["temporal_veto"] = "previous_range_has_no_temporal_journal"
        return None
    session.temporal_latches = clock
    return journal


def accepted_temporal(session, index, native_lt_state):
    journal = session.temporal
    if journal is None:
        return
    try:
        current = capture_domains(session.engine,
            current_latches=session.temporal_latches.at(index), native_lt_state=native_lt_state)
        from .transition_anchor_causality import capture_anchor_state
        journal.record(index, current, causal=capture_anchor_state(session.engine))
    except (TypeError, ValueError, OverflowError):
        clear_temporal(session)
        session.stats["temporal_veto"] = "unrepresented_current_temporal_value"
        return
    session.stats["temporal"] = dict(journal.stats)


def export_temporal(session, stop):
    journal = session.temporal
    if journal is None or journal.last < stop - 1:
        return None
    record = journal.export()
    record["last"] = stop - 1
    return record


def clear_temporal(session):
    journal = getattr(session, "temporal", None)
    if journal is not None:
        journal.store.clear()
        session.temporal = None
