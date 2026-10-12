"""Reference-audio clip surface over its original geometry, peaks and gestures.

R27: native/elided title, a separate waveform body, compact time footer and
actual focus/drag feedback. Reuses existing Qt/theme resources and raster;
no second audio, timeline, selection model or persistent fields.
"""
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPen, QPolygonF

from .. import qascade_theme as theme

_HEADER_HEIGHT = 18
_FOOTER_HEIGHT = 16


@lru_cache(maxsize=32)
def _color(value, alpha=255):
    color = QColor(value)
    color.setAlpha(alpha)
    return color


@lru_cache(maxsize=32)
def _pen(value, width=1):
    return QPen(_color(value), width)


def waveform_body(view, left, right):
    return QRectF(left + 1, _HEADER_HEIGHT, max(0, right - left - 2),
                  max(1, view.height() - _HEADER_HEIGHT - _FOOTER_HEIGHT))


def paint_view(view):
    painter = QPainter(view)
    try:
        painter.fillRect(view.rect(), _color(theme.CANVAS_BG))
        if view._loading:
            paint_empty(painter, view, "正在加载波形…")
            return
        if view._peaks is None or view._duration <= 0:
            paint_empty(painter, view, "暂无音频")
            return
        geometry = view._clip_geometry()
        paint_clip(painter, view, geometry)
        px = int(view._playhead * geometry[4] - view._scroll_x)
        if 0 <= px < view.width():
            painter.setPen(_pen(theme.CANVAS_SELECTION, 2))
            painter.drawLine(px, 0, px, view.height())
    finally:
        painter.end()


def paint_empty(painter, view, text):
    painter.setFont(theme.cached_font(theme.FS_SMALL))
    painter.setPen(_pen(theme.CANVAS_MUTED))
    painter.drawText(view.rect(), Qt.AlignmentFlag.AlignCenter, text)


def paint_clip(painter, view, geometry):
    x0, x1 = geometry[:2]
    left, right = max(0, x0), min(view.width(), x1)
    if right <= left:
        return
    frame = QRectF(left, 1, right - left, view.height() - 2)
    painter.fillRect(frame, _color(theme.CANVAS_CARD))
    header = QRectF(left, 1, right - left, _HEADER_HEIGHT - 1)
    fill = theme.CANVAS_SELECTION_FILL if view._is_dragging else theme.CANVAS_HEADER
    painter.fillRect(header, _color(fill))
    paint_waveform(painter, view, geometry, waveform_body(view, left, right))
    paint_fades(painter, view, geometry)
    paint_label(painter, view, left, right)
    selected = view._is_dragging or view.hasFocus()
    painter.setPen(_pen(theme.CANVAS_SELECTION if selected else theme.CANVAS_BORDER,
                        2 if view._is_dragging else 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(frame.adjusted(.5, .5, -.5, -.5))


def paint_waveform(painter, view, geometry, body):
    pixmap = view._ensure_waveform_pixmap(view.height())
    if pixmap is None or pixmap.isNull() or body.width() <= 0:
        return
    _x0, _x1, _fi, _fo, pps, duration = geometry
    start = view._clip_start
    t0 = max(0, (body.left() + view._scroll_x) / pps - start)
    t1 = min(duration, (body.right() + view._scroll_x) / pps - start)
    if t1 <= t0:
        return
    first = t0 / duration * pixmap.width()
    last = max(first + 1, t1 / duration * pixmap.width())
    painter.drawPixmap(body, pixmap, QRectF(first, 0, last - first, pixmap.height()))


def paint_label(painter, view, left, right):
    if right - left <= 8:
        return
    painter.setFont(theme.cached_font(theme.FS_SMALL, True))
    painter.setPen(_pen(theme.CANVAS_INK))
    text = QFontMetrics(painter.font()).elidedText(
        view._label or "音频", Qt.TextElideMode.ElideRight, max(0, right - left - 8))
    painter.drawText(QRectF(left + 4, 1, right - left - 8, _HEADER_HEIGHT - 1),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
    paint_footer(painter, view, left, right)


def paint_footer(painter, view, left, right):
    if right - left <= 20:
        return
    parts = []
    if view._clip_start > .01:
        parts.append(f"{view._clip_start:.2f}s")
    if view._clip_fade_in > .01:
        parts.append(f"IN {view._clip_fade_in:.2f}s")
    if view._clip_fade_out > .01:
        parts.append(f"OUT {view._clip_fade_out:.2f}s")
    painter.setFont(theme.cached_font(theme.FS_SMALL))
    painter.setPen(_pen(theme.CANVAS_MUTED))
    text = QFontMetrics(painter.font()).elidedText(
        " · ".join(parts), Qt.TextElideMode.ElideRight, max(0, right - left - 20))
    painter.drawText(QRectF(left + 10, view.height() - _FOOTER_HEIGHT, right - left - 20,
                            _FOOTER_HEIGHT - 1), Qt.AlignmentFlag.AlignVCenter, text)


def paint_fades(painter, view, geometry):
    x0, x1, fade_in, fade_out, pps, _duration = geometry
    endpoints = (x0 + int(fade_in * pps), x1 - int(fade_out * pps))
    painter.save()
    painter.setClipRect(waveform_body(view, max(0, x0), min(view.width(), x1)))
    painter.setPen(Qt.PenStyle.NoPen)
    for left, right, is_in in ((x0, endpoints[0], True), (endpoints[1], x1, False)):
        if right <= left:
            continue
        gradient = QLinearGradient(left, 0, right, 0)
        gradient.setColorAt(0, _color(theme.CANVAS_MUTED, 100 if is_in else 12))
        gradient.setColorAt(1, _color(theme.CANVAS_MUTED, 12 if is_in else 100))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(QRectF(left, _HEADER_HEIGHT, right - left,
                                view.height() - _HEADER_HEIGHT - _FOOTER_HEIGHT))
    painter.restore()
    paint_handles(painter, view, endpoints)


def paint_handles(painter, view, endpoints):
    painter.setPen(_pen(theme.CANVAS_BORDER))
    painter.setBrush(_color(theme.CANVAS_SELECTION))
    cy = view.height() - 8
    for cx in endpoints:
        if 0 <= cx <= view.width():
            painter.drawPolygon(QPolygonF([QPointF(cx, cy - 6), QPointF(cx + 6, cy),
                                           QPointF(cx, cy + 6), QPointF(cx - 6, cy)]))
