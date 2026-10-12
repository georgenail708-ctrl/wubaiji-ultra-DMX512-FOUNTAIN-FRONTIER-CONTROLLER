"""Shared native editor regions for R185's remaining workspace families.

Reuse: Qt Widgets / official Flow Layout example, existing ivory palette,
Editor regions and operator layouts. No data or timers.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QLabel, QLayout, QScrollArea, QSizePolicy, QSplitter, QWidget,
)

from . import qascade_theme as theme
from .effect_workspace_style import decorate_tree, ivory_palette, recolor_qss
from .flow_layout import FlowLayout
from .editor_materials import apply_material, deep_region, is_deep

COLORS = theme.EDITOR_IVORY


def surface(widget, *, decorate=False, role=None):
    """Apply editor chrome only to the explicitly supplied context region."""
    if is_deep(widget):
        return
    if decorate:
        decorate_tree(widget)
    widget.setPalette(ivory_palette())
    widget.setStyleSheet(recolor_qss(widget.styleSheet()))
    apply_material(widget, kind=role or ("shell" if widget.isWindow() else "panel"))


def caption(label, *, strong=False):
    label.setWordWrap(True)
    label.setMinimumHeight(24)
    label.setMaximumHeight(16777215)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    color = COLORS["ink"] if strong else COLORS["muted"]
    weight = 600 if strong else 400
    label.setStyleSheet(f"color:{color};background:transparent;"
                       f"font-size:11px;font-weight:{weight};padding:4px 8px;")


def flow_row(widget):
    """Move the original layout items once; widgets and their signals survive."""
    old = widget.layout()
    if isinstance(old, FlowLayout):
        return old
    margins = old.contentsMargins()
    items = []
    while old.count():
        items.append(old.takeAt(0))
    carrier = QWidget(widget)
    carrier.hide()
    carrier.setLayout(old)
    flow = FlowLayout(widget)
    flow.setContentsMargins(margins)
    flow.setSpacing(6)
    for item in items:
        flow.addItem(item)
    widget.setMinimumHeight(0)
    widget.setMaximumHeight(16777215)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    carrier.deleteLater()
    return flow


def inspector_scroll(content, *, minimum=200):
    content.setMinimumWidth(minimum)
    content.setMaximumWidth(16777215)
    content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    if content.layout() is not None:
        content.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(content)
    scroll.setMinimumWidth(minimum)
    surface(scroll)
    return scroll


def editor_split(split, sizes):
    split.setChildrenCollapsible(False)
    split.setHandleWidth(6)
    name = split.objectName() or f"editorSplit{id(split)}"
    split.setObjectName(name)
    split.setStyleSheet(
        f"QSplitter#{name}::handle{{background:{COLORS['root']};}}"
        f"QSplitter#{name}::handle:hover{{background:{COLORS['border']};}}")
    split.setSizes(sizes)


def horizontal_pair(first, second, *, sizes):
    split = QSplitter(Qt.Orientation.Horizontal)
    split.addWidget(first)
    split.addWidget(second)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 0)
    editor_split(split, sizes)
    return split


def context_controls(root):
    """Style original form controls while retaining custom painted dark wells."""
    from PyQt6.QtWidgets import (
        QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTabWidget,
    )

    types = (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTabWidget)
    surface(root)
    for widget in root.findChildren(QWidget):
        if is_deep(widget):
            continue
        if isinstance(widget, types):
            decorate_tree(widget)
        elif isinstance(widget, QLabel):
            widget.setStyleSheet(recolor_qss(widget.styleSheet()))
            widget.setPalette(ivory_palette())


def popup(dialog):
    """Explicit dialog chrome; preserve every original field and accept slot."""
    if not isinstance(dialog, QWidget):
        return dialog
    context_controls(dialog)
    if dialog.layout() is not None:
        dialog.layout().setContentsMargins(12, 12, 12, 12)
        dialog.layout().setSpacing(8)
    return dialog
