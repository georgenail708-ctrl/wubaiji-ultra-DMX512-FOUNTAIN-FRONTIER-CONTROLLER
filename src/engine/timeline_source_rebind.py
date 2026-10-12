"""Explicit Main restoration lease; full source validation stays on the worker.

Reuse the original epoch, all-cache runtime purge and mutable input signature.
An equivalent project shell is eligible for retention only at the two actual
restoration call sites; arbitrary project replacement remains destructive.
"""
from .timeline_clip_invalidation import begin_clip_invalidation, end_clip_invalidation


def recheck_restored_timeline(engine, previous, current):
    if engine is None:
        return False
    from .timeline_cache_notifications import _same_published_owner, _metadata_matches
    published = getattr(engine, "_timeline_published_inputs", {})
    ref = getattr(engine, "_timeline_auto_baker_ref", None)
    baker = ref() if ref is not None else None
    if (baker is None or baker._stop or baker._engine is not engine
            or engine._pm.project is not current
            or not _same_published_owner(engine, previous, published)
            or not _metadata_matches(engine, current, published)
            or published["layout"] != engine._cached_project_clip_layout_signature(current.tracks or [])):
        return False
    lease = begin_clip_invalidation(engine, retire_inputs=True)
    try:
        with baker._epoch_lock:
            engine._timeline_source_rebind = {
                "previous": previous, "current": current, "published": published,
                "purge_pending": True,
            }
        engine.invalidate_all_caches()
    finally:
        end_clip_invalidation(lease)
    return True


def pending_owner(engine, project, published):
    pending = getattr(engine, "_timeline_source_rebind", None)
    return (type(pending) is dict and pending.get("published") is published
            and pending.get("previous") is published.get("source")
            and pending.get("current") is project and engine._pm.project is project)


def purge_check_context(engine):
    from .timeline_cache_notifications import _same_published_owner, _metadata_matches
    pending = getattr(engine, "_timeline_source_rebind", None)
    if type(pending) is not dict or not pending.get("purge_pending"):
        return None
    ref = getattr(engine, "_timeline_auto_baker_ref", None)
    baker = ref() if ref is not None else None
    current, published = engine._pm.project, getattr(engine, "_timeline_published_inputs", {})
    if (baker is None or baker._stop or not pending_owner(engine, current, published)
            or not _same_published_owner(engine, current, published)
            or not _metadata_matches(engine, current, published)
            or published["layout"] != engine._cached_project_clip_layout_signature(current.tracks or [])):
        engine._timeline_source_rebind = None
        return None
    pending["purge_pending"] = False
    return baker, current
