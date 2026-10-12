"""Versioned terrain mapping of relative band levels, not PCM dBFS.

Use the standard fixed-reference amplitude mapping documented by librosa
0.11.0 amplitude_to_db (ISC). Existing NumPy ufuncs perform the small 8-band
calculation without importing librosa or normalizing each frame to its peak.
"""
import math

import numpy as np

from .matrix_pixel_audio_modes import _mapped_levels


def terrain_levels(engine):
    levels = _mapped_levels(engine, 8, state_key="music")
    if int(getattr(engine, "audio_controls_version", 0)) < 1:
        return levels
    if float(engine.audio_amplitude) <= 0.0:
        return np.zeros_like(levels)
    decibels = 20.0 * np.log10(np.maximum(levels, np.float32(1e-4)))
    height = np.clip((decibels + 80.0) / 80.0, 0.0, 1.0)
    return np.where(levels > np.float32(1e-4), height, 0.0)


def terrain_release(engine, previous, levels, dt, duration):
    if int(getattr(engine, "audio_controls_version", 0)) < 1:
        return np.maximum(levels, previous - dt / duration)
    return levels + (previous - levels) * math.exp(-dt / duration)


def single_axis_field(axis, heights, width):
    centers = (np.arange(8, dtype=np.float32) + 0.5) / 8.0
    weights = np.exp(-0.5 * ((axis[:, None] - centers) / width) ** 2)
    return weights.dot(heights) / np.maximum(weights.sum(axis=1), 1e-6)


def terrain_field(engine, along, cross, heights, width):
    legacy = int(getattr(engine, "audio_controls_version", 0)) < 1
    if not legacy and float(engine.audio_amplitude) <= 0.0:
        return np.zeros_like(along)
    if legacy or (np.ptp(along) > 1e-6 and np.ptp(cross) > 1e-6):
        indices = np.minimum(7, (cross * 8).astype(int))
        height = heights[indices]
        return np.exp(-0.5 * ((along - height) / width) ** 2) * (height > 1e-6)
    axis = along if np.ptp(along) > 1e-6 else cross
    return single_axis_field(axis, heights, width)
