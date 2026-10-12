"""Publication foundation using original frame stores and freshness gates.

Playback activation awaits a legal temporal adapter. Full publication stays
authoritative. No writing store or worker QObject is exposed to live playback.
Reference: original owned writer, checkpoints and complete publication gate.
"""
from __future__ import annotations

from contextlib import nullcontext
import time

from .timeline_bake_inputs import check_cancel, input_signature, inputs_current
from .timeline_cache_read import prepare_cache_reads
from .timeline_publication_inputs import publication_inputs_current
from .timeline_clip_invalidation import clip_invalidation_pending
from .timeline_frame_store import TimelineFrameStore
from .timeline_render_metadata import metadata_signature


def _same_revision(baker, generation, revision):
    return (not baker._stop and baker._gen == generation
            and baker._input_revision == revision
            and not clip_invalidation_pending(baker))


def handoff_options(baker, project, generation, revision):
    def permission():
        return (_same_revision(baker, generation, revision)
                and baker._engine._pm.project is project
                and baker._engine_is_playing())

    def retired():
        return (not _same_revision(baker, generation, revision)
                or baker._engine._pm.project is not project)

    return {"_ready_permission": permission, "_ready_retired": retired,
            "_ready_generation": generation, "_ready_revision": revision}


def _owned_permission(engine, session, reporter):
    if session is None or not session.enabled:
        return False
    if not getattr(engine, "_timeline_bake_owned", False):
        return False
    permission = session.opts.get("_ready_permission")
    if permission is None or not permission():
        return False
    package = type(session).__module__.rsplit(".", 1)[0]
    from .timeline_owned_reporter import default_owned_progress
    return default_owned_progress(reporter, package)


def _seal_index_valid(session, seal):
    return (seal + 1 <= session.count
            and seal in session.frames and seal in session.records)


def seal_owned_prefix(engine, session, reporter):
    """Called at the original CE abort boundary, still on its owning writer."""
    if not _owned_permission(engine, session, reporter):
        return False
    points = session.points.export()
    if not points:
        return False
    seal = max(points)
    if not _seal_index_valid(session, seal):
        return False
    if seal + 1 == session.count:
        session.finish(False)  # Original complete record: all math is done.
    from .temporal_journal_range import export_temporal
    session.opts["_ready_handoff"] = {
        "temporal": export_temporal(session, seal + 1),
        "inputs": session.inputs, "interval": session.interval,
        "end": session.end, "count": session.count, "stop": seal + 1,
        "points": points, "frames": session.frames, "records": session.records,
        "seal_point": points[seal],
    }
    return True


def _closed_store(store, stop):
    return (type(store) is TimelineFrameStore and store._writer_owner is None
            and len(store) >= stop)


def _closed_temporal(record):
    temporal = record.get("temporal")
    if temporal is None:
        return True  # No temporal certificate: playback adapter must veto.
    return (temporal["last"] == record["stop"] - 1
            and _closed_store(temporal["store"], record["stop"] + 1))


def _closed_pairs(record):
    return (0 < record["stop"] <= record["count"]
            and all(_closed_store(record[key], record["stop"]) for key in ("frames", "records"))
            and _closed_temporal(record))


def _clear_unpublished(record):
    if record is not None:
        record["frames"].clear()
        record["records"].clear()
        temporal = record.get("temporal")
        if type(temporal) is dict:
            temporal["store"].clear()


def _worker_pair(worker, record):
    worker._timeline_render_frame_cache = record["frames"]
    worker._timeline_render_record_source_frame_cache = record["records"]


def _full_worker_current(worker, inputs):
    return (not worker._precompile_dirty
            and worker._cached_project_clip_layout_signature(inputs["project"].tracks or []) == inputs["layout"]
            and worker._timeline_render_runtime_signature_value() == inputs["runtime"])


def _finish_full_worker_metadata(worker, inputs, record):
    # Only the worker assignments skipped by the interrupted original builder.
    _worker_pair(worker, record)
    worker._timeline_render_frame_cache_signature = inputs["layout"]
    worker._timeline_render_frame_cache_interval = record["interval"]
    worker._timeline_render_frame_cache_end = record["end"]
    worker._timeline_render_lock_signature = inputs["layout"]
    worker._timeline_render_runtime_signature = inputs["runtime"]
    worker._timeline_render_lock_misses = 0


def _publish_terminal(live, worker, inputs, record, certificate_opts):
    if not _full_worker_current(worker, inputs):
        return "stale"
    _finish_full_worker_metadata(worker, inputs, record)
    from .timeline_bake_context import _publish
    return _publish(live, worker, inputs, certificate_opts)


def _certified_metadata(record, inputs, opts):
    return dict(source=inputs["source"], token=inputs["token"],
                metadata=inputs["metadata"], runtime=inputs["runtime"],
                layout=inputs["layout"], interval_ms=inputs["interval"],
                generation=opts["_ready_generation"],
                revision=opts["_ready_revision"], certified=True)


def _live_temporal_current(record):
    from .temporal_journal import DOMAINS, BLOCK_FRAMES
    temporal = record.get("temporal")
    return (type(temporal) is dict and temporal.get("schema") == 1
            and temporal.get("domains") == DOMAINS and temporal.get("block") == BLOCK_FRAMES
            and temporal.get("causal_anchor_schema") == 1
            and temporal.get("last") == record["stop"] - 1
            and _closed_store(temporal.get("store"), record["stop"] + 1))


def _attach_live_seal(record):
    """Derive only after owning SQL close; CE points stay original/unmodified.

    This mark does not permit cache serving. Source/input/epoch publication
    remains the original following gate; no future prepared Python is a live
    target exit. The sparse native CE continuation still lives in points.
    """
    record["live_seal_certified"] = False
    if not _live_temporal_current(record):
        return
    from .temporal_journal import read_complete_temporal
    from .timeline_temporal_exit import flatten_complete
    from .timeline_incremental_checkpoint import copy_tree
    started = time.perf_counter()
    legal = flatten_complete(copy_tree(read_complete_temporal(record["temporal"], record["stop"] - 1)))
    point = dict(record["seal_point"])
    point["python"] = legal
    record["seal_point"] = point
    record["live_seal_certified"] = True
    record["temporal"]["stats"]["live_seal_seconds"] = time.perf_counter() - started


def _publish_ready(live, worker, inputs, record, certificate_opts):
    # The actual writer has closed before this helper can be reached.
    _attach_live_seal(record)
    _worker_pair(worker, record)
    prepare_cache_reads(worker, certificate_opts)
    if not publication_inputs_current(live, inputs, certificate_opts, current=inputs_current):
        return "stale"
    with certificate_opts.get("publish_lock", nullcontext()):
        check_cancel(certificate_opts)
        if live._pm.project is not inputs["source"]:
            return "stale"
        record.update(_certified_metadata(record, inputs, certificate_opts))
        live._timeline_ready_range = record
        live._timeline_ready_served = None
    return "partial"


def publish_handoff(live, worker, inputs, opts):
    """Atomic lease after writer close; original gate alone publishes full."""
    from .playback_engine import TimelineBakeAbort
    record = opts.pop("_ready_handoff", None)
    if record is None:
        return "aborted"
    published = False
    try:
        if inputs is None or worker is None or not _closed_pairs(record):
            return "aborted"
        certificate_opts = dict(opts)
        certificate_opts["should_abort"] = opts["_ready_retired"]
        publisher = _publish_terminal if record["stop"] == record["count"] else _publish_ready
        status = publisher(live, worker, inputs, record, certificate_opts)
        published = status in ("partial", "done")
        return status
    except TimelineBakeAbort:
        return "aborted"
    finally:
        if not published:
            _clear_unpublished(record)


def _metadata_current(engine, project, ready):
    return (ready.get("source") is project
            and ready.get("metadata") == metadata_signature(project)
            and ready.get("runtime") == engine._timeline_render_runtime_signature_value()
            and ready.get("interval_ms") == engine._timer.interval())


def ready_metadata_matches(engine, project):
    ready = getattr(engine, "_timeline_ready_range", None)
    return type(ready) is dict and _metadata_current(engine, project, ready)


def retire_serving(engine):
    ready = getattr(engine, "_timeline_ready_range", None)
    if type(ready) is dict:
        ready["certified"] = False
    engine._timeline_ready_served = None


def _ready_for_check(baker, project):
    if not vars(baker).get("_pending_input_check", False):
        return None
    engine = baker._engine
    ready = getattr(engine, "_timeline_ready_range", None)
    return ready if type(ready) is dict and _metadata_current(engine, project, ready) else None


def _check_token(baker, project, ready, opts):
    if input_signature(project, opts) == ready["token"]:
        return True
    with baker._epoch_lock:
        if not opts["should_abort"]():
            baker._pending_input_check = False
    return False


def _recertify_ready(baker, project, ready, *, generation, revision):
    engine = baker._engine
    with baker._epoch_lock:
        if not _same_revision(baker, generation, revision):
            return True
        if getattr(engine, "_timeline_ready_range", None) is not ready:
            return False
        if not _metadata_current(engine, project, ready):
            return False
        ready.update(generation=generation, revision=revision, certified=True)
        baker._pending_input_check = False
    return True


def reuse_ready_bake(baker, project):
    """Read all mutable input bytes on the existing serial worker."""
    ready = _ready_for_check(baker, project)
    if ready is None:
        return False
    generation, revision = baker._gen, baker._input_revision
    opts = {"should_abort": lambda: not _same_revision(baker, generation, revision),
            "input_yield": lambda: time.sleep(.0015)}
    from .playback_engine import TimelineBakeAbort
    try:
        if not _check_token(baker, project, ready, opts):
            return False
    except TimelineBakeAbort:
        return True
    return _recertify_ready(baker, project, ready, generation=generation, revision=revision)


def _legal_previous(record, current):
    from .timeline_incremental_inputs import dirty_start
    if type(record) is not dict or not record.get("points"):
        return None
    dirty, _reason = dirty_start(record, current)
    if dirty <= 0.:
        return None
    legal = [index for index in record["points"]
             if (index + 1) * record["interval"] <= dirty]
    return (max(legal), record) if legal else None


def choose_previous(engine, current):
    """Reuse original input comparison; never choose a future dirty exit."""
    candidates = [getattr(engine, "_timeline_incremental_complete", None),
                  getattr(engine, "_timeline_ready_range", None)]
    matches = [_legal_previous(record, current) for record in candidates]
    acceptable = [pair for pair in matches if pair is not None]
    return max(acceptable, key=lambda pair: pair[0])[1] if acceptable else candidates[0]
