"""Full-result temporal authority follows the original accepted publication.

This certificate borrows existing closed stores. It creates no writer or store,
does not certify partial/full flags and does no journal read under epoch lock.
"""
from .timeline_ready_range import _live_temporal_current, _closed_store


def _pair_current(engine, published, complete):
    return (complete["frames"] is published["frames"] is engine._timeline_render_frame_cache
            and complete["records"] is published["records"] is engine._timeline_render_record_source_frame_cache)


def _closed_complete(complete):
    if type(complete) is not dict or not complete.get("count"):
        return False
    count = complete["count"]
    return (_closed_store(complete.get("frames"), count)
            and _closed_store(complete.get("records"), count)
            and _live_temporal_current(dict(complete, stop=count)))


def attach_full_temporal(engine):
    """Caller has just accepted original full inputs or unchanged full token."""
    engine._timeline_full_temporal_certificate = None
    engine._timeline_full_temporal_served = None
    published = getattr(engine, "_timeline_published_inputs", None)
    complete = getattr(engine, "_timeline_incremental_complete", None)
    if type(published) is not dict or not _closed_complete(complete):
        return
    if not engine._timeline_render_cache_locked or not _pair_current(engine, published, complete):
        return
    certificate = dict(complete, stop=complete["count"], mode="full", published=published,
                       complete=complete, source=published["source"], layout=published["layout"],
                       runtime=published["runtime"], interval_ms=published["interval"])
    engine._timeline_full_temporal_certificate = certificate


def full_temporal_current(engine):
    record = getattr(engine, "_timeline_full_temporal_certificate", None)
    if type(record) is not dict or not engine._timeline_render_cache_locked:
        return None
    if record["source"] is not engine._pm.project or not _closed_complete(record["complete"]):
        return None
    if getattr(engine, "_timeline_published_inputs", None) is not record["published"]:
        return None
    if getattr(engine, "_timeline_incremental_complete", None) is not record["complete"]:
        return None
    if not _pair_current(engine, record["published"], record["complete"]):
        return None
    return record if _runtime_current(engine, record) else None


def _runtime_current(engine, record):
    return (engine._timer.interval() == record["interval_ms"]
            and engine._timeline_render_runtime_signature_value() == record["runtime"]
            and engine._cached_project_clip_layout_signature(record["source"].tracks or []) == record["layout"])
