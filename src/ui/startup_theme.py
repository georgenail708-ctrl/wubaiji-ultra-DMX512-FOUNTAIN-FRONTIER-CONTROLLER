"""Startup uses the same ivory roles as ordinary editor chrome.

The original static artwork remains a single GUI-owned pixmap; Qt scales its
paint coordinates through devicePixelRatio. Animation and branding stay in main.
Reference: Qt v6.11.0 qpixmap.cpp devicePixelRatio/setDevicePixelRatio.
"""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from .qascade_theme import EDITOR_IVORY

SPLASH_PALETTE = EDITOR_IVORY


def build_splash_background(size, ratio):
    width, height = size
    base = QPixmap(round(width * ratio), round(height * ratio))
    base.setDevicePixelRatio(ratio)
    base.fill(Qt.GlobalColor.transparent)
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(EDITOR_IVORY['border']), 1))
    painter.setBrush(QColor(EDITOR_IVORY['panel']))
    painter.drawRoundedRect(QRectF(.5, .5, width - 1, height - 1), 12, 12)
    gradient = QLinearGradient(0, height, width, 0)
    for position, role in ((0, 'panel'), (.55, 'surface'), (1, 'panel')):
        gradient.setColorAt(position, QColor(EDITOR_IVORY[role]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(1, 1, width - 2, height - 2), 11, 11)
    grid = QColor(EDITOR_IVORY['border'])
    grid.setAlpha(15)
    painter.setPen(QPen(grid, 1))
    for x in range(46, width, 46):
        painter.drawLine(x, 0, x, height)
    for y in range(46, height, 46):
        painter.drawLine(0, y, width, y)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(EDITOR_IVORY['panel']))
    painter.drawRect(QRectF(1, height - 64, width - 2, 63))
    divider = QColor(EDITOR_IVORY['border'])
    divider.setAlpha(70)
    painter.setPen(QPen(divider, 1))
    painter.drawLine(0, height - 64, width, height - 64)
    painter.end()
    return base
