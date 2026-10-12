"""Bounded full-source waveform raster and DPR-aware existing view cache.

Uses installed NumPy maximum.reduceat/take and Qt/hidpi APIs, no upstream copy.
R27 actual PCM RED and API references: r189-studio-audio-visual.md.
The decoded peaks remain untouched; no transport or analysis state lives here.
"""
from functools import lru_cache

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from .. import qascade_theme as theme
from ..hidpi import hidpi_pixmap, screen_dpr


def peak_columns(peaks, width):
    """Map the complete decoded peak span into bounded columns, retaining peaks."""
    width = max(1, int(width))
    source = np.asarray(peaks).reshape(-1)
    if source.size == 0:
        return np.zeros(width, dtype=np.float32)
    starts = np.arange(width, dtype=np.int64) * source.size // width
    values = np.maximum.reduceat(source, starts) if width <= source.size else source[starts]
    return np.clip(np.nan_to_num(values, copy=True, nan=0, posinf=1, neginf=0), 0, 1)


@lru_cache(maxsize=8)
def _wave_pen(color):
    return QPen(QColor(color), 1)


def _render(view, width, height):
    columns = peak_columns(view._peaks, width)
    pixmap = hidpi_pixmap(width, height, view)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setPen(_wave_pen(theme.CANVAS_MUTED))
        middle = height // 2
        for x, amplitude in enumerate(columns):
            bar_height = max(1, int(float(amplitude) * (height - 4)))
            painter.drawLine(x, middle - bar_height // 2, x, middle + bar_height // 2)
    finally:
        painter.end()
    return pixmap


def ensure_waveform_pixmap(view, height):
    peaks, duration = view._peaks, float(view._duration or 0)
    if peaks is None or duration <= 0 or len(peaks) < 2:
        return None
    height = max(24, int(height))
    width = max(600, min(view._PIXMAP_MAX_W, int(duration * view._PIXMAP_BASE_PPS)))
    key = (duration, len(peaks), height, width, screen_dpr(view),
           theme.CANVAS_REVISION, theme.CANVAS_MUTED)
    if view._waveform_pixmap is not None and view._waveform_cache_key == key:
        return view._waveform_pixmap
    view._waveform_pixmap = _render(view, width, height)
    view._waveform_cache_key = key
    return view._waveform_pixmap
