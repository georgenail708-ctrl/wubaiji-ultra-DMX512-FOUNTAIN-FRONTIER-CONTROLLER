"""Retire this engine's actual private frame stores after producers stop.

Reuse TimelineFrameStore.clear and DiskCache's per-thread close/context contract.
No cleanup belongs to ordinary Stop, All Clear, invalidation or cache serving.
"""
import threading

from .timeline_frame_store import TimelineFrameStore

_CACHE_FIELDS = ("_timeline_render_frame_cache", "_timeline_render_record_source_frame_cache")
_CONTINUATIONS = ("_timeline_incremental_complete", "_timeline_incremental_candidate", "_timeline_incremental_previous")


def _join_generated_readers(engine):
    current, seen = threading.current_thread(), []
    for name in ("_gen_thread", "_gen_zombie"):
        thread = getattr(engine, name, None)
        # These fields own Python generated-reader threads. A foreign adapter
        # without an actual reader must not synthesize one during retirement.
        if not isinstance(thread, threading.Thread) or any(thread is previous for previous in seen):
            continue
        seen.append(thread)
        if thread is current:
            raise RuntimeError("Frame store retirement cannot run on its active generated reader")
        thread.join()
        if thread.is_alive():
            raise RuntimeError("Generated reader survived shutdown before frame store retirement")


def _owned_stores(engine):
    stores = [getattr(engine, name, None) for name in _CACHE_FIELDS]
    for name in (*_CONTINUATIONS, "_timeline_ready_range", "_timeline_full_temporal_certificate"):
        record = getattr(engine, name, None)
        if type(record) is dict:
            stores.extend(record.get(key) for key in ("frames", "records"))
            temporal = record.get("temporal")
            if type(temporal) is dict:
                stores.append(temporal.get("store"))
    history = getattr(engine, "_timeline_ready_history", None)
    if type(history) is dict and type(history.get("record")) is dict:
        stores.extend(_history_stores(history["record"]))
    return stores


def _unique_stores(stores):
    unique = []
    for store in stores:
        if isinstance(store, TimelineFrameStore) and not any(store is previous for previous in unique):
            unique.append(store)
    return unique


def retire_engine_frame_stores(engine):
    """Called only after the original window producer shutdown, before deletion."""
    if engine is None:
        return
    _join_generated_readers(engine)
    unique = _unique_stores(_owned_stores(engine))
    for store in unique:
        store.clear()
    # Release checkpoint metadata only after every actual store retired safely.
    for name in (*_CONTINUATIONS, "_timeline_ready_range", "_timeline_ready_served",
                 "_timeline_full_temporal_certificate", "_timeline_full_temporal_served",
                 "_timeline_ready_history", "_timeline_ready_active_plan"):
        if hasattr(engine, name):
            setattr(engine, name, None)


def _history_stores(record):
    stores = [record.get(key) for key in ("frames", "records")]
    temporal = record.get("temporal")
    if type(temporal) is dict:
        stores.append(temporal.get("store"))
    return stores
