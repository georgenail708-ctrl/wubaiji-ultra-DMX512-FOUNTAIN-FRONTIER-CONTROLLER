"""Project pitch origins onto actual fixture geometry without changing notes.

The original normalized MIDI identity stays in point.y. Only the visible
travel origin uses geometry; gain, lifetime, width and threshold stay upstream.
Uses existing NumPy/grid APIs, no audio analysis, animation source or cache.
"""
import numpy as np


def pitch_point(engine, note):
    if int(getattr(engine, "audio_controls_version", 0)) < 1:
        return 0.0, note
    from .matrix_pixel_music_arrangements import _coordinates
    along, cross = _coordinates(engine)
    if not along.size:
        return 0.0, note
    axis = cross if float(np.ptp(cross)) > 1e-6 else along
    distance = np.abs(axis - note)
    origin = float(np.min(along[distance <= float(distance.min()) + 1e-6]))
    return origin, note


def pitch_distance(engine, axes, point, motion):
    along, cross = axes
    origin, note = point
    drift, width = motion
    if int(getattr(engine, "audio_controls_version", 0)) < 1:
        location = min(1.0, drift)
        return ((cross - note) / width) ** 2 + ((along - location) / width) ** 2
    location = origin + (1.0 - origin) * min(1.0, drift)
    if float(np.ptp(cross)) <= 1e-6:
        return ((along - location) / width) ** 2
    return ((cross - note) / width) ** 2 + ((along - location) / width) ** 2
