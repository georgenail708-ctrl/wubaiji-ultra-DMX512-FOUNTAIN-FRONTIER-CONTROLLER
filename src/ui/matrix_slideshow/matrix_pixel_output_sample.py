"""Shared pixel output conversion and paused video sampling."""

from typing import Any, Optional

import numpy as np


_MISSING = object()


def pixel_output_values(engine: Any, weights: np.ndarray) -> np.ndarray:
    """Apply the original tick_values output stages in their original order."""
    weights = engine._apply_wings_blocks_array(weights)
    weights = engine._apply_audio_modulation_array(weights)
    on = weights >= float(engine.threshold)
    if engine.invert:
        on = np.logical_not(on)
    values = (on.astype(np.uint8, copy=False) * np.uint8(255))
    return engine.apply_output_gamma_values(values)


def _held_audio_output_values(engine: Any, weights: np.ndarray) -> np.ndarray:
    """Reuse the held envelope without integrating another audio frame."""
    state = engine._effect_state
    key = "_audio_mod_frame_env"
    original = state.get(key, _MISSING)
    if original is _MISSING or original is None:
        held_env = state.get("_audio_mod_env", 0.0)
        state[key] = 0.0 if held_env is None else held_env
    try:
        return pixel_output_values(engine, weights)
    finally:
        if original is _MISSING:
            state.pop(key, None)
        else:
            state[key] = original


def paused_video_output_values(engine: Any) -> Optional[np.ndarray]:
    """Sample the retained video frame through the original output stages."""
    if engine._mode != "video_sample":
        return None
    if engine._running or engine.video_playing:
        return None
    if engine._video_frame_cache is None:
        return None
    weights = engine._compute_video_sample_array()
    return _held_audio_output_values(engine, weights)


def refresh_paused_video_canvas(panel: Any) -> None:
    """Refresh paused video canvas values before the owner's existing submit."""
    if not getattr(panel, "_pixel_paused", False) or panel._pixel_running:
        return
    values = paused_video_output_values(panel._pixel_engine)
    if values is None:
        return
    panel._canvas.set_pixel_values(values, panel._pixel_engine.threshold)
