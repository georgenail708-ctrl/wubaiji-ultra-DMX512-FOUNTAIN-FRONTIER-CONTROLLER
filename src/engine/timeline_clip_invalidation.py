"""Serialize clip invalidation admission with the existing AutoBaker epoch.

A pending runtime purge permits neither cached serving nor numerical bake
admission. Full mutable source validation is still submitted by the original
purge completion path; this module never certifies owners or source content.
"""

def begin_clip_invalidation(engine, *, retire_inputs=False):
    ref = getattr(engine, "_timeline_auto_baker_ref", None)
    baker = ref() if ref is not None else None
    if baker is None or baker._stop or baker._engine is not engine:
        return None
    with baker._epoch_lock:
        baker._clip_invalidation_depth = vars(baker).get("_clip_invalidation_depth", 0) + 1
        if retire_inputs:
            baker._input_revision += 1
            engine._timeline_render_cache_locked = False
    return baker


def end_clip_invalidation(baker):
    if baker is not None:
        with baker._epoch_lock:
            baker._clip_invalidation_depth -= 1
        baker._wake.set()


def clip_invalidation_pending(baker):
    # Queue truth uses only bool(pointer/set), never iteration or mutation. The
    # drain takes a depth lease before swapping those queue pointers, so there
    # is no queue-empty/purge-not-entered gap. No inverse queue lock is acquired.
    engine = baker._engine
    return (vars(baker).get("_clip_invalidation_depth", 0) > 0
            or bool(getattr(engine, "_gen_pending_invalidate_clips", ()))
            or bool(getattr(engine, "_gen_pending_invalidate_all", False)))


def purge_clip_geometry_caches(engine, keyset):
    """Keep the four original cache traversals and per-key deletion order."""
    for name in ("_clip_fade_geometry_cache", "_clip_xy_reference_cache",
                 "_clip_render_cache", "_clip_compiled_cache"):
        for key in [key for key in getattr(engine, name)
                    if isinstance(key, tuple) and key and str(key[0]) in keyset]:
            del getattr(engine, name)[key]


