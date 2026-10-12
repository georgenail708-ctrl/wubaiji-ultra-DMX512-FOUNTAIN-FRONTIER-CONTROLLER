"""Ivory Effects cards on the original source model and W2 delegate.

References: Qt QListView/QStyledItemDelegate and the bundled Tabler MIT icons.
The glyph is a functional illustration, never a sampled effect or DMX preview.
"""
from __future__ import annotations

import weakref
from functools import lru_cache

from PyQt6 import sip
from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QFontMetrics, QPen
from PyQt6.QtWidgets import QStyle

from . import qascade_theme as _th
from .hidpi import screen_dpr

CARD_HEIGHT = 116
HEADER_HEIGHT = 28
TWO_COLUMN_WIDTH = 640
_DELEGATE_CLS = None


@lru_cache(maxsize=64)
def _color(value):
    return QColor(value)


@lru_cache(maxsize=64)
def _brush(value):
    return QBrush(_color(value))


@lru_cache(maxsize=128)
def _pen(value, width=1.0, style=Qt.PenStyle.SolidLine):
    pen = QPen(_color(value))
    pen.setWidthF(width)
    pen.setStyle(style)
    return pen


def _role_pen(role, width=1.0, style=Qt.PenStyle.SolidLine):
    return _pen(_th.EDITOR_IVORY[role], width, style)


def _role_brush(role):
    return _brush(_th.EDITOR_IVORY[role])


def _font(kind):
    if kind == "title":
        return _th.cached_font(px=16, bold=True, families=_th.DISPLAY_FONT_FAMILIES)
    if kind == "header":
        return _th.cached_font(px=13, bold=True)
    if kind == "category":
        return _th.cached_font(px=11)
    return _th.cached_font(px=12)


@lru_cache(maxsize=32)
def _metrics(px, bold, families):
    return QFontMetrics(_th.cached_font(px=px, bold=bold, families=families))


def _title_metrics():
    return _metrics(16, True, _th.DISPLAY_FONT_FAMILIES)


def _viewport_width(delegate, option):
    view = delegate.parent()
    if view is None or not hasattr(view, "viewport"):
        view = option.widget
    if view is not None and hasattr(view, "viewport"):
        return max(1, view.viewport().width())
    return max(1, option.rect.width())


def _tabler_glyph(name, size, color, dpr):
    # Reuse the existing bounded SVG/DPR cache with this viewport's screen,
    # rather than the public loader's primary-screen DPR.
    from .main_icon_library import _tabler_pixmap_cached

    return _tabler_pixmap_cached(name, size, color, round(dpr, 3))


def _glyph_for_index(index, *, color, dpr, size=56):
    from .effect_browser_w2_proxy import preset_at
    from .effect_icon_library import effect_icon_name_for_preset

    preset = preset_at(index.model(), index.row())
    if preset is None:
        return None
    return _tabler_glyph(effect_icon_name_for_preset(preset), size, color, dpr)


@lru_cache(maxsize=1)
def _filled_star_path():
    from .effect_browser import STAR_PX, _star_polygon_path

    return _star_polygon_path(QRect(0, 0, STAR_PX, STAR_PX))


def _paint_star(painter, card_rect, index, dpr):
    from .effect_browser import favorite_star_rect, _row_is_favorite

    rect = favorite_star_rect(card_rect)
    painter.save()
    if _row_is_favorite(index):
        painter.translate(rect.topLeft())
        painter.setPen(_role_pen("accent"))
        painter.setBrush(_role_brush("accent"))
        painter.drawPath(_filled_star_path())
    else:
        pixmap = _tabler_glyph("star", rect.width(), _th.EDITOR_IVORY["muted"], dpr)
        painter.drawPixmap(rect.topLeft(), pixmap)
    painter.restore()


def _paint_surface(painter, body, state):
    selected = bool(state & QStyle.StateFlag.State_Selected)
    hovered = bool(state & QStyle.StateFlag.State_MouseOver)
    surface = "selected" if selected else "surface"
    if hovered and not selected:
        surface = "hover"
    painter.setPen(_role_pen("border"))
    painter.drawLine(body.left() + 8, body.bottom() + 1,
                     body.right() - 8, body.bottom() + 1)
    painter.setBrush(_role_brush(surface))
    painter.setPen(_role_pen("accent" if selected else "border"))
    painter.drawRoundedRect(QRectF(body), 8, 8)
    if state & QStyle.StateFlag.State_HasFocus:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_role_pen("focus", style=Qt.PenStyle.DotLine))
        painter.drawRoundedRect(QRectF(body.adjusted(3, 3, -3, -3)), 6, 6)


def _paint_glyph(painter, body, index, option):
    size = 28 if body.width() < 280 else 56
    plate = QRect(body.left() + 12, body.top() + 12, size + 16, size + 16)
    painter.setBrush(_role_brush("input"))
    painter.setPen(_role_pen("border"))
    painter.drawRoundedRect(QRectF(plate), 8, 8)
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    color = _th.EDITOR_IVORY["accent" if enabled else "disabled"]
    pixmap = _glyph_for_index(index, color=color, dpr=screen_dpr(option.widget), size=size)
    if pixmap is not None:
        painter.drawPixmap(plate.left() + 8, plate.top() + 8, pixmap)


def _paint_text(painter, body, index, state):
    from .effect_browser import _CategoryNameRole, _DescriptionRole

    left = body.left() + (68 if body.width() < 280 else 96)
    width = max(0, body.right() - left - 12)
    enabled = bool(state & QStyle.StateFlag.State_Enabled)
    painter.setPen(_role_pen("ink" if enabled else "disabled"))
    painter.setFont(_font("title"))
    name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
    title = _title_metrics().elidedText(name, Qt.TextElideMode.ElideRight, width)
    painter.drawText(QRect(left, body.top() + 12, width, 24),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
    painter.setFont(_font("body"))
    painter.setPen(_role_pen("muted" if enabled else "disabled"))
    description = str(index.data(_DescriptionRole) or "")
    painter.drawText(QRect(left, body.top() + 40, width, 32),
                     int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                     | int(Qt.TextFlag.TextWordWrap), description)
    painter.setFont(_font("category"))
    category = str(index.data(_CategoryNameRole) or "")
    metrics = _metrics(11, False, _th.UI_FONT_FAMILIES)
    label = metrics.elidedText(category, Qt.TextElideMode.ElideRight, width)
    painter.drawText(QRect(left, body.top() + 76, width, 16),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)


def _paint_header(painter, option, index):
    from .effect_browser import _CategoryCountRole, _CategoryNameRole

    painter.fillRect(option.rect, _role_brush("panel"))
    painter.setPen(_role_pen("border"))
    painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
    painter.setFont(_font("header"))
    painter.setPen(_role_pen("muted"))
    name = str(index.data(_CategoryNameRole) or "")
    count = str(index.data(_CategoryCountRole) or 0)
    painter.drawText(option.rect.adjusted(12, 0, -12, 0),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                     f"{name} · {count}")


def _delegate_class():
    global _DELEGATE_CLS
    if _DELEGATE_CLS is not None:
        return _DELEGATE_CLS
    from . import effect_browser_w2 as _w2

    class IvoryEffectLibraryDelegate(_w2._delegate_class()):
        """The original W2 paint dispatch, with an owned native card skin."""

        def __init__(self, parent=None, panel=None):
            super().__init__(parent, panel)

        @property
        def _w2_panel(self):
            panel = self._panel_ref() if self._panel_ref is not None else None
            return panel if panel is not None and not sip.isdeleted(panel) else None

        @_w2_panel.setter
        def _w2_panel(self, panel):
            self._panel_ref = weakref.ref(panel) if panel is not None else None

        def _paint_header(self, painter, option, index):
            _paint_header(painter, option, index)

        def _paint_item(self, painter, option, index):
            body = option.rect.adjusted(3, 1, -4, -3)
            _paint_surface(painter, body, option.state)
            _paint_glyph(painter, body, index, option)
            _paint_text(painter, body, index, option.state)
            _paint_star(painter, option.rect, index, screen_dpr(option.widget))

        def sizeHint(self, option, index):  # noqa: N802 (Qt API)
            from .effect_browser import _ItemTypeRole

            width = _viewport_width(self, option)
            if index.data(_ItemTypeRole) == "header":
                return QSize(width, HEADER_HEIGHT)
            columns = 2 if width >= TWO_COLUMN_WIDTH else 1
            # Qt's IconMode flow limit is QRect.right() (width - 1).
            # Leave that last pixel free so two exact halves do not wrap.
            return QSize(max(1, (width - 1) // columns), CARD_HEIGHT)

    _DELEGATE_CLS = IvoryEffectLibraryDelegate
    return _DELEGATE_CLS


def make_delegate(parent, panel):
    """Return an owned delegate; model, row identity and W2 behavior stay original."""
    return _delegate_class()(parent, panel)


__all__ = ["make_delegate"]
