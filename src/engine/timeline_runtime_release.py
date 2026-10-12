"""Release a stopped playback session without retiring authored render stores.

Reuse existing hold/takeover and Draw-history resets. Static compiled input,
complete frame/record-source stores, freshness signatures and checkpoints stay
owned by the timeline renderer. Call after all live writers have stopped.
"""
from __future__ import annotations

from .audio_mod_draw_dsp import clear_caches as clear_draw_history
from .clip_takeover import reset_playback_consumption


_RUNTIME_MAPS = (
    "_light_tracking_state", "_light_tracking_time", "_light_tracking_color_cache",
    "_lt_warned", "_fade_in_anchor", "_fade_in_anchor_deps",
    "_clip_output_filter_state",
)


def _reset_native_light_tracking(engine):
    state = getattr(engine, "_lt_c_state", None)
    if state is None:
        return
    # Reset the same object shared by live hooks and prepared K2 bundles.
    state.clear_state()
    for name in ("color", "color_valid", "touched_color"):
        getattr(state, name).fill(0)


def release_playback_runtime(engine):
    """Start the next playback with empty dynamic history and unchanged inputs."""
    reset_playback_consumption(engine)
    for name in _RUNTIME_MAPS:
        getattr(engine, name, {}).clear()
    _reset_native_light_tracking(engine)
    clear_draw_history()
    engine._last_render_time = None
    engine._last_output_universes = set()
    engine._last_output_universes_by_layer = {}
    engine._last_record_source_outputs_by_universe = {}
    engine._ltp_takeover_pending = False
    engine._ltp_takeover_scope = None
    scheduler = getattr(engine, "_precharge_scheduler", None)
    if scheduler is not None:
        scheduler.reset()
