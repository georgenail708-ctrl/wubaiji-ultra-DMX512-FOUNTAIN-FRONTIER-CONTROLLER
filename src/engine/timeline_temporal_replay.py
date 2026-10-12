"""Original-frame replay inside its existing nonrecursive lock.

No full-show snapshot/compile, new worker, SQL writer or copied render formula.
Missing-frame count and actual elapsed cost are mandatory runtime evidence.
Long seeks have O(missing frames) cost: no default-performance claim here.
"""
from contextvars import ContextVar
from contextlib import contextmanager
import threading
import time


_OUTER_RENDER_OWNER = ContextVar("qascade_outer_render_owner", default=None)
_OWNED_REPLAY = ContextVar("qascade_owned_temporal_replay", default=None)
_SHADOW_FIELDS = ("_nm", "_position", "_suppress_position_emit",
                  "_timeline_render_cache_building_lock", "_timeline_render_cache_locked",
                  "_ltp_takeover_pending",
                  "_ltp_takeover_layer", "_ltp_takeover_scope",
                  "_last_output_universes_by_layer", "_last_output_universes",
                  "_last_record_source_outputs_by_universe", "_precharge_scheduler")


def outer_owns_lock(engine):
    owner = _OUTER_RENDER_OWNER.get()
    return owner is not None and owner[0] is engine and owner[1] == threading.get_ident()


def claim_outer_lock(engine):
    _OUTER_RENDER_OWNER.set((engine, threading.get_ident(), _OUTER_RENDER_OWNER.get()))


def retire_outer_lock(engine):
    owner = _OUTER_RENDER_OWNER.get()
    if not outer_owns_lock(engine):
        raise RuntimeError("only the actual acquiring thread can retire render ownership")
    _OUTER_RENDER_OWNER.set(owner[2])


def replay_owns_lock(engine):
    owner = _OWNED_REPLAY.get()
    return (owner is not None and owner[0] is engine
            and owner[1] == threading.get_ident() and outer_owns_lock(engine))


def _start_shadow(engine):
    from qascade_ce import _K2FrameCacheNetworkProxy
    engine._nm = _K2FrameCacheNetworkProxy(engine._nm, build_mode=True)
    engine._suppress_position_emit = True
    engine._timeline_render_cache_building_lock = True
    engine._timeline_render_cache_locked = False
    engine._ltp_takeover_pending = False
    engine._last_output_universes_by_layer = {}
    engine._last_output_universes = set()
    engine._last_record_source_outputs_by_universe = {}
    engine._precharge_scheduler = None


@contextmanager
def _original_frame_shadow(engine):
    if not outer_owns_lock(engine) or replay_owns_lock(engine):
        raise RuntimeError("temporal replay requires outer original render ownership")
    saved = {name: (hasattr(engine, name), getattr(engine, name, None)) for name in _SHADOW_FIELDS}
    token = _OWNED_REPLAY.set((engine, threading.get_ident()))
    try:
        _start_shadow(engine)
        yield
    finally:
        _OWNED_REPLAY.reset(token)
        for name, (present, value) in saved.items():
            if present:
                setattr(engine, name, value)
            elif hasattr(engine, name):
                delattr(engine, name)


def replay_missing(engine, ready, start, stop):
    """The caller restores a certified exit before start; never reset last_time.

    Current outer cursor/LTP/author NM and output-presence dictionaries are
    restored by identity. Actual temporal state and Draw owner advance through
    the original pipeline. Only the final outer target emits actual output.
    """
    if start >= stop:
        return
    from qascade_ce.native_source_motion_clock import range_position
    started = time.perf_counter()
    with _original_frame_shadow(engine):
        for index in range(start, stop):
            position = range_position(index, ready["interval"], ready["end"])
            engine._render_position(position, layer_name="clip_playback")
    stats = ready.setdefault("live_cost", {})
    stats["replay_frames"] = stats.get("replay_frames", 0) + stop - start
    stats["replay_seconds"] = stats.get("replay_seconds", 0.) + time.perf_counter() - started
