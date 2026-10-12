"""Bounded neutral canvas resources; owner QPainter draws behind its content.

Reference: existing Qt ObjectBoundingMode brushes / hidpi caches, 网格底纹
hierarchy and independent material analysis. No child film,
screen sampling, timers, output state or graph model.
"""
from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QGradient, QLinearGradient

from . import qascade_theme as theme


@lru_cache(maxsize=32)
def tone(value: str, alpha: int = 255) -> QColor:
    color = QColor(value)
    color.setAlpha(alpha)
    return color


@lru_cache(maxsize=16)
def gradient(top: str, bottom: str) -> QBrush:
    ramp = QLinearGradient(0.0, 0.0, 0.0, 1.0)
    ramp.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
    ramp.setColorAt(0.0, tone(top))
    ramp.setColorAt(1.0, tone(bottom))
    return QBrush(ramp)


def paint_bed(painter, rect) -> None:
    """Subtle warm editing bed; glyphs and real output never enter this brush."""
    painter.fillRect(rect, gradient(theme.CANVAS_BG_TOP, theme.CANVAS_BG_BOTTOM))


def selection_fill(alpha: int = 35) -> QColor:
    return tone(theme.BRAND_ORANGE, alpha)


def paint_card_face(painter, rect, radius: float) -> None:
    """Small cached shadow and reflected edge, inside the owner's paint order."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(tone(theme.CANVAS_INK, 22))
    painter.drawRoundedRect(QRectF(rect).translated(0.0, 2.0), radius, radius)
    painter.setBrush(gradient(theme.CANVAS_CARD, theme.CANVAS_CARD_BOTTOM))
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()
