"""Bounded background-only blur/refraction for explicit QWidget material owners.

Reuse: Qt QGraphicsBlurEffect + QGraphicsScene.render, QPainter pixmap sampling.
No QGraphicsView, QWidget.render/grab, desktop capture, timer, worker or text blur.
Reference: background/foreground separation and distortion;
Qt's blur API is reused, website SVG/source is not copied (licence unverified).
"""
from __future__ import annotations

from collections import OrderedDict
from math import ceil, cos, sin, sqrt
import weakref

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsScene, QWidget

MAX_ITEMS = 8
MAX_FRAME_ITEMS = 32
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PIXELS = 300_000
MAX_FRAME_PIXELS = 1_000_000
MAX_FRAME_EDGE = 2048
MAX_TILES = 4096
_SOURCES = OrderedDict()
_FRAMES = OrderedDict()
_COUNTERS = {"renders": 0, "hits": 0}


def _bytes(pixmap):
    return pixmap.width() * pixmap.height() * 4


def _trim(cache, budget, items=MAX_ITEMS):
    while cache and (len(cache) > items
                     or sum(_bytes(value[-1]) for value in cache.values()) > budget):
        cache.popitem(last=False)


def _limited_source(surface):
    pixels = surface.width() * surface.height()
    if pixels <= MAX_SOURCE_PIXELS:
        return QPixmap(surface)
    scale = sqrt(MAX_SOURCE_PIXELS / pixels)
    return surface.scaled(max(1, int(surface.width() * scale)),
                          max(1, int(surface.height() * scale)),
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)


def set_background_surface(owner, surface, *, revision=0):
    """Owner explicitly supplies background-only pixels; never capture its widget.

    Surface logical size must cover owner.rect(). Caller must advance revision on
    theme/content changes. Replacing or clearing a surface invalidates old frames.
    A scene/data/foreground screenshot is outside this API's source contract.
    """
    if not isinstance(owner, QWidget) or not isinstance(surface, QPixmap):
        raise TypeError("Background requires a QWidget owner and explicit QPixmap")
    clear_background_surface(owner)
    if surface.isNull():
        return
    logical_size = surface.deviceIndependentSize()
    source = _limited_source(surface)
    key = (id(owner), source.cacheKey(), str(revision))
    _SOURCES[key] = (weakref.ref(owner), logical_size, source)
    owner._editor_background_key = key
    _trim(_SOURCES, MAX_SOURCE_BYTES)


def clear_background_surface(owner):
    key = getattr(owner, "_editor_background_key", None)
    if key is None:
        return
    _SOURCES.pop(key, None)
    for frame_key in tuple(_FRAMES):
        if frame_key[0] == key:
            _FRAMES.pop(frame_key)
    owner._editor_background_key = None


def provide_editor_background(owner, colors):
    """Explicit root material supplies its own stable abstract lighting surface.

    No widget contents are sampled. This is the actual root paint background;
    child materials sample the same coordinates, retaining foreground isolation.
    """
    dpr = min(3.0, max(1.0, owner.devicePixelRatioF()))
    revision = (owner.width(), owner.height(), round(dpr, 3),
                colors["root"], colors["surface"], colors["input"])
    if (getattr(owner, "_editor_background_revision", None) == revision
            and getattr(owner, "_editor_background_key", None) in _SOURCES):
        return
    width, height = max(1, owner.width()), max(1, owner.height())
    scale = min(dpr, sqrt(MAX_SOURCE_PIXELS / (width * height)),
                MAX_FRAME_EDGE / max(width, height))
    surface = QPixmap(max(1, int(width * scale)), max(1, int(height * scale)))
    painter = QPainter(surface)
    gradient = QLinearGradient(0, 0, surface.width(), surface.height())
    for stop, key in ((0, "root"), (0.40, "root"), (0.44, "surface"),
                      (0.48, "root"), (1, "input")):
        gradient.setColorAt(stop, QColor(colors[key]))
    painter.fillRect(surface.rect(), gradient)
    painter.end()
    surface.setDevicePixelRatio(scale)
    set_background_surface(owner, surface, revision=revision)
    owner._editor_background_revision = revision


def background_pixmap(owner):
    source = _SOURCES.get(getattr(owner, "_editor_background_key", None))
    return source[-1] if source is not None else None


def _background(widget):
    while isinstance(widget, QWidget):
        key = getattr(widget, "_editor_background_key", None)
        source = _SOURCES.get(key)
        if source is not None and source[0]() is widget:
            _SOURCES.move_to_end(key)
            return key, widget, source[1], source[2]
        widget = widget.parentWidget()
    return None


def prepare_backdrop(widget, *, blur_radius=4.0, refraction=8.0):
    """Prepare once on show/resize/DPR/theme/revision; paint only reads the key.

    Returns None without an explicitly provided ancestor background. Frame keys
    retain no QWidget or pixmap reference, so eviction enforces the byte bound.
    Foreground widgets and live canvases are never rendered into the source.
    """
    background = _background(widget)
    if background is None or widget.width() < 1 or widget.height() < 1:
        return None
    source_key, owner, logical_size, source = background
    point = widget.mapTo(owner, QPoint(0, 0))
    dpr = min(3.0, max(1.0, widget.devicePixelRatioF()))
    blur = min(12.0, max(0.0, float(blur_radius)))
    strength = min(12.0, max(0.0, float(refraction)))
    key = (source_key, point.x(), point.y(), widget.width(), widget.height(),
           round(dpr, 3), blur, strength)
    if key in _FRAMES:
        _FRAMES.move_to_end(key)
        _COUNTERS["hits"] += 1
        return key
    frame = _render_frame(source, logical_size, key)
    _FRAMES[key] = (frame,)
    _COUNTERS["renders"] += 1
    _trim(_FRAMES, MAX_FRAME_BYTES, MAX_FRAME_ITEMS)
    return key


def backdrop_pixmap(key):
    """Paint-time cache lookup only; a missing/evicted frame uses material fallback."""
    frame = _FRAMES.get(key)
    if frame is None:
        return None
    _FRAMES.move_to_end(key)
    return frame[-1]


def prepare_painted_background(widget):
    """Original custom painter prepares the same background cache outside paint."""
    if widget.isVisible():
        if widget.isWindow():
            from .qascade_theme import EDITOR_IVORY
            provide_editor_background(widget, EDITOR_IVORY)
        widget._editor_painted_backdrop = prepare_backdrop(widget)


def paint_background(painter, widget, fallback, dirty=None):
    """Cache-only original painter seam; foreground and hit geometry stay original."""
    frame = backdrop_pixmap(getattr(widget, "_editor_painted_backdrop", None))
    if frame is None:
        painter.fillRect(dirty if dirty is not None else widget.rect(), fallback)
        return
    painter.drawPixmap(QRectF(widget.rect()), frame, QRectF(frame.rect()))


def _render_frame(source, logical_size, key):
    _, x, y, width, height, dpr, blur, strength = key
    scale = min(dpr, sqrt(MAX_FRAME_PIXELS / max(1, width * height)),
                MAX_FRAME_EDGE / max(width, height))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    padding = ceil((blur * 3 + strength + 2) * scale)
    crop = _crop_source(source, logical_size, (x, y, width, height), size, padding)
    softened = _soften(crop, blur * scale)
    return _refract(softened, size, padding, strength * scale)


def _crop_source(source, logical_size, region, size, padding):
    x, y, width, height = region
    out = QPixmap(size[0] + padding * 2, size[1] + padding * 2)
    out.fill(Qt.GlobalColor.transparent)
    sx = source.width() / max(1.0, logical_size.width())
    sy = source.height() / max(1.0, logical_size.height())
    px = padding * width / size[0]
    py = padding * height / size[1]
    sample = QRectF((x - px) * sx, (y - py) * sy,
                    (width + px * 2) * sx, (height + py * 2) * sy)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(out.rect()), source, sample)
    painter.end()
    return out


def _soften(source, radius):
    if radius <= 0:
        return source
    scene = QGraphicsScene()
    scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
    item = scene.addPixmap(source)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    rect = QRectF(source.rect())
    scene.setSceneRect(rect)
    softened = QPixmap(source.size())
    softened.fill(Qt.GlobalColor.transparent)
    painter = QPainter(softened)
    scene.render(painter, rect, rect, Qt.AspectRatioMode.IgnoreAspectRatio)
    painter.end()
    scene.clear()
    return softened


def _refract(source, size, padding, strength):
    out = QPixmap(*size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    step = max(16, ceil(max(size) / sqrt(MAX_TILES)))
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            target = QRectF(x, y, min(step, size[0] - x), min(step, size[1] - y))
            sample = _refracted_tile(target, size, padding, strength)
            painter.drawPixmap(target, source, sample)
    painter.end()
    return out


def _refracted_tile(target, size, padding, strength):
    """Smooth static displacement; no copy of the website's SVG fractal filter."""
    nx = target.center().x() * 2 / size[0] - 1
    ny = target.center().y() * 2 / size[1] - 1
    dx = sin(ny * 3.7 + 92) * cos(nx * 2.1) * strength * (0.25 + 0.75 * abs(nx) ** 8)
    dy = cos(nx * 4.1 + 92) * sin(ny * 2.7) * strength * (0.25 + 0.75 * abs(ny) ** 8)
    return target.translated(padding + dx, padding + dy)


def backdrop_cache_stats():
    """Read-only evidence for actual cache budget/hit assertions."""
    return {**_COUNTERS, "source_items": len(_SOURCES), "frame_items": len(_FRAMES),
            "source_bytes": sum(_bytes(item[-1]) for item in _SOURCES.values()),
            "frame_bytes": sum(_bytes(item[-1]) for item in _FRAMES.values()),
            "max_items": MAX_ITEMS, "max_frame_items": MAX_FRAME_ITEMS,
            "max_source_bytes": MAX_SOURCE_BYTES,
            "max_frame_bytes": MAX_FRAME_BYTES}
