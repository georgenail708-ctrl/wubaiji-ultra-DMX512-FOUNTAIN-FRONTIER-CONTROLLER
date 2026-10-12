"""Distinguish saving edits from changing render inputs, with worker validation.

The UI compares metadata only. An unknown edit immediately disables serving;
the existing serial AutoBaker then reads the full mutable frame content before
recertifying an unchanged published store. Object identity alone is not proof.
"""
from __future__ import annotations

import logging
import time

from .timeline_bake_inputs import input_signature
from .timeline_render_metadata import metadata_signature

log = logging.getLogger(__name__)


def remember_published_inputs(engine, inputs):
    """Record exactly the input token and stores accepted by the publication gate."""
    engine._timeline_published_inputs = {
        "source": inputs["source"], "token": inputs["token"],
        "metadata": inputs["metadata"], "runtime": inputs["runtime"],
        "layout": inputs["layout"], "interval": inputs["interval"],
        "frames": engine._timeline_render_frame_cache,
        "records": engine._timeline_render_record_source_frame_cache,
    }


def _same_published_owner(engine, project, published):
    from .timeline_source_rebind import pending_owner
    return ((published.get("source") is project or pending_owner(engine, project, published))
            and published.get("frames") is getattr(engine, "_timeline_render_frame_cache", None)
            and published.get("records") is getattr(engine, "_timeline_render_record_source_frame_cache", None)
            and bool(published.get("frames")))


def _metadata_matches(engine, project, published):
    return (published.get("metadata") == metadata_signature(project)
            and published.get("runtime") == engine._timeline_render_runtime_signature_value()
            and published.get("interval") == engine._timer.interval())


def _same_inputs(engine, project, published):
    return (_same_published_owner(engine, project, published)
            and _metadata_matches(engine, project, published))


def _authored_keys_match(keys, project):
    if not keys or any(not key for key in keys):
        return False
    authored = {str(clip.id) for track in project.tracks or [] for clip in track.clips or []}
    return set(keys).issubset(authored)


def clip_store_check_context(engine, keys):
    """Retain completed owners only for a bound worker's mandatory recheck.

    Metadata/layout equality permits retaining storage, never serving it. The
    caller unlocks first and submits this context after its runtime purge.
    Without a live bound worker, keep the original destructive invalidation.
    """
    ref = getattr(engine, "_timeline_auto_baker_ref", None)
    baker = ref() if ref is not None else None
    if baker is None or baker._stop or baker._engine is not engine:
        return None
    project = engine._pm.project
    published = getattr(engine, "_timeline_published_inputs", {})
    try:
        if not _authored_keys_match(keys, project):
            return None  # Preserve the historic empty-prefix/all-clips API.
        if not _same_inputs(engine, project, published):
            return None
        if published["layout"] != engine._cached_project_clip_layout_signature(project.tracks or []):
            return None
        return baker, project
    except Exception:
        log.exception("clip store retention check failed; retire conservatively")
        return None


def notify_project_change(baker, project, *, force=False, engine=None):
    """Retire every notified edit, then validate unchanged render dependencies.

    The compatible force flag identifies a clip notification, not proof that
    its numeric inputs changed. Equal metadata still requires full mutable
    frame hashing on the worker before any completed store is recertified.
    """
    if baker is None:
        if engine is not None:
            engine.unlock_timeline_render_cache()
        return
    engine = baker._engine
    published = getattr(engine, "_timeline_published_inputs", {})
    check_only = False
    try:
        from .timeline_ready_range import ready_metadata_matches
        check_only = ready_metadata_matches(engine, project)
        if _same_published_owner(engine, project, published):
            check_only = _metadata_matches(engine, project, published)
    except Exception:
        log.exception("render metadata check failed; retire inputs conservatively")
    if check_only:
        baker.schedule_invalidate(check_only=True)
    else:
        baker.schedule_invalidate()


def _revision_changed(baker, generation, revision):
    return (baker._stop or baker._gen != generation
            or vars(baker).get("_input_revision", 0) != revision)


def _certify_unchanged(baker, project, published, generation, revision):
    engine = baker._engine
    with baker._epoch_lock:
        if _revision_changed(baker, generation, revision):
            return True  # A newer edit will be handled by the next pass.
        if not _same_published_owner(engine, project, published):
            return False
        if not _metadata_matches(engine, project, published):
            return False
        engine._timeline_render_frame_cache_signature = published["layout"]
        engine._timeline_render_lock_signature = published["layout"]
        engine._timeline_render_runtime_signature = published["runtime"]
        engine._timeline_render_cache_locked = True
        published["source"] = project
        engine._timeline_source_rebind = None
        baker._baked_gen = generation
        baker._failed_gen = -1
        baker._pending_input_check = False
        from .timeline_full_temporal import attach_full_temporal
        attach_full_temporal(engine)
    return True


def reuse_published_bake(baker, project):
    """Run only on the serial bake worker, after the edit debounce expires."""
    if not vars(baker).get("_pending_input_check", False):
        return False
    generation, revision = baker._gen, baker._input_revision
    engine = baker._engine
    published = getattr(engine, "_timeline_published_inputs", {})
    if not _same_inputs(engine, project, published):
        return False
    opts = {
        "should_abort": lambda: _revision_changed(baker, generation, revision),
        "input_yield": lambda: time.sleep(.0015),
    }
    try:
        if input_signature(project, opts) != published["token"]:
            with baker._epoch_lock:
                if not _revision_changed(baker, generation, revision):
                    baker._pending_input_check = False
            return False
    except Exception as error:
        from .playback_engine import TimelineBakeAbort
        if isinstance(error, TimelineBakeAbort):
            return True
        raise
    return _certify_unchanged(baker, project, published, generation, revision)
