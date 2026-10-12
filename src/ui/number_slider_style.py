"""Cached colors for the numeric slider.

The paint geometry, value conversion and native gestures stay in widget_kit.
References: Qt QPalette/QPainter; R185 effect-library adoption; original theme.
"""
from __future__ import annotations

from PyQt6.QtGui import QBrush, QColor, QPen

from . import qascade_theme as theme

_STYLES = {}


def numeric_style(*, ivory=True):
    cached = _STYLES.get(bool(ivory))
    if cached is not None:
        return cached
    roles = _roles(ivory)
    colors = {key: QColor(value) for key, value in roles.items()}
    cached = {
        "border": QPen(colors["border"], 1),
        "base": QBrush(colors["base"]),
        "fill": QBrush(colors["fill"]),
        "drag": QBrush(colors["drag"]),
        "hover": QBrush(colors["hover"]),
        "text": QPen(colors["text"]),
        "selected_text": QPen(colors["selected_text"]),
        "shadow": QPen(QColor(0, 0, 0, 0 if ivory else 160)),
    }
    _STYLES[bool(ivory)] = cached
    return cached


def _roles(ivory):
    if ivory:
        colors = theme.EDITOR_IVORY
        return {"border": colors["border"], "base": colors["input"],
                "fill": colors["selected"], "drag": colors["hover"],
                "hover": colors["selected"], "text": colors["ink"],
                "selected_text": colors["ink"]}
    return {"border": theme.OUTPUT_BORDER_NORMAL, "base": theme.OUTPUT_BG_INPUT,
            "fill": theme.SELECT_BLUE, "drag": theme.SELECT_BLUE_HOVER,
            "hover": theme.SLIDER_BLUE_HOVER, "text": theme.OUTPUT_TEXT_PRIMARY,
            "selected_text": theme.OUTPUT_TEXT_SELECTED_FG}
