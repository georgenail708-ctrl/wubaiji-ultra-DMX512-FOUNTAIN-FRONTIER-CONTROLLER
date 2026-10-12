"""P01 native materials; original controls, values and owners are retained.

Design: r185-programmer-design-review.md, Programmer round2 B.
impeccable Operate. Qt QWidget/QPalette APIs; existing MIT Tabler assets.
Reference: https://doc.qt.io/qt-6/qpalette.html
No upstream code, bitmap controls, renderer or output state is introduced.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QScrollArea,
)

from . import qascade_theme as theme
from .widget_kit import scope_qss


def ivory_surface(widget):
    colors = theme.EDITOR_IVORY
    palette = QPalette(widget.palette())
    roles = {"Window": "panel", "WindowText": "ink", "Base": "input",
             "AlternateBase": "surface", "Text": "ink", "Button": "surface",
             "ButtonText": "ink", "Light": "surface", "Midlight": "panel",
             "Dark": "border", "Mid": "border", "Shadow": "border",
             "Highlight": "selected", "HighlightedText": "ink"}
    for role, color in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[color]))
    widget.setPalette(palette)
    scope_qss(widget, f"background:{colors['panel']};border:none;")


def ivory_label(label, *, title=False):
    colors = theme.EDITOR_IVORY
    label.setFont(theme.cached_font(13 if title else 12, bold=title))
    label.setStyleSheet(
        f"QLabel{{color:{colors['ink'] if title else colors['muted']};"
        f"background:transparent;border:none;font-size:{13 if title else 12}px;}}")


def ivory_button(button, *, danger=False, compact=False):
    colors = theme.EDITOR_IVORY
    ink = colors["danger"] if danger else colors["ink"]
    padding = "0px" if compact else "2px 8px"
    button.setStyleSheet(
        f"QAbstractButton{{background:{colors['surface']};color:{ink};"
        f"border:1px solid {colors['border']};border-radius:{theme.RADIUS}px;"
        f"padding:{padding};font-size:12px;min-height:0px;}}"
        f"QAbstractButton:hover{{background:{colors['hover']};}}"
        f"QAbstractButton:checked{{background:{colors['selected']};"
        f"border-color:{colors['accent']};color:{colors['ink']};}}"
        f"QAbstractButton:focus{{border-color:{colors['focus']};}}"
        f"QAbstractButton:disabled{{color:{colors['disabled']};"
        f"background:{colors['panel']};}}")


def ivory_field(field):
    colors = theme.EDITOR_IVORY
    field.setStyleSheet(
        f"QComboBox,QAbstractSpinBox,QLineEdit{{background:{colors['input']};"
        f"color:{colors['ink']};border:1px solid {colors['border']};"
        f"border-radius:{theme.RADIUS}px;padding:2px 6px;font-size:12px;}}"
        f"QComboBox:focus,QAbstractSpinBox:focus,QLineEdit:focus{{"
        f"border-color:{colors['focus']};}}"
        f"QComboBox:disabled,QAbstractSpinBox:disabled,QLineEdit:disabled{{"
        f"color:{colors['disabled']};}}"
        f"QComboBox QAbstractItemView{{background:{colors['surface']};"
        f"color:{colors['ink']};selection-background-color:{colors['selected']};"
        f"selection-color:{colors['ink']};}}")


def ivory_control(widget):
    if isinstance(widget, QLabel):
        ivory_label(widget)
    elif isinstance(widget, QAbstractButton):
        ivory_button(widget, danger=widget.text() == "Release")
    elif isinstance(widget, (QComboBox, QAbstractSpinBox, QLineEdit)):
        ivory_field(widget)


def ivory_scroll(scroll):
    colors = theme.EDITOR_IVORY
    scroll.setStyleSheet(
        f"QScrollArea{{background:{colors['panel']};border:none;}}"
        f"QScrollBar:vertical{{background:{colors['panel']};width:10px;border:none;}}"
        f"QScrollBar::handle:vertical{{background:{colors['border']};"
        f"border-radius:{theme.RADIUS}px;min-height:24px;}}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
        "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
        f"QScrollBar:horizontal{{background:{colors['panel']};height:10px;border:none;}}"
        f"QScrollBar::handle:horizontal{{background:{colors['border']};"
        f"border-radius:{theme.RADIUS}px;min-width:24px;}}"
        "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0px;}"
        "QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent;}")


def group_row_qss(color):
    colors = theme.EDITOR_IVORY
    return (
        f"QPushButton{{background:{colors['panel']};color:{colors['ink']};"
        f"border:1px solid transparent;border-left:3px solid {color};"
        f"border-radius:{theme.RADIUS}px;padding:4px 8px;text-align:left;"
        "font-size:12px;min-height:34px;}"
        f"QPushButton:hover{{background:{colors['hover']};}}"
        f"QPushButton:checked{{background:{colors['selected']};"
        f"color:{colors['ink']};border-top-color:{colors['border']};"
        f"border-right-color:{colors['border']};border-bottom-color:{colors['border']};}}"
        f"QPushButton:focus{{border-top-color:{colors['focus']};"
        f"border-right-color:{colors['focus']};border-bottom-color:{colors['focus']};}}"
        f"QPushButton:disabled{{color:{colors['disabled']};}}")


def object_column(column, selector):
    ivory_surface(column)
    ivory_surface(selector)
    ivory_surface(selector._btn_container)
    for index in (0, 1):
        ivory_surface(selector.layout().itemAt(index).widget())
    for label in selector.findChildren(QLabel):
        ivory_label(label, title=label.text() == "设备组")
    for button in (selector._all_btn, selector._light_mode_btn_single,
                   selector._light_mode_btn_whole):
        ivory_button(button)
    for scroll in selector.findChildren(QScrollArea):
        ivory_scroll(scroll)


def dark_labels(row):
    row._title.setFont(theme.cached_font(13, bold=True))
    row._title.setStyleSheet(
        f"QLabel{{color:{theme.TEXT_PRIMARY};font-size:13px;font-weight:bold;"
        "background:transparent;border:none;}")
    for label in row._range_value_labels.values():
        label.setFont(theme.cached_font(12, families=("Consolas",)))
        label.setStyleSheet(label.styleSheet().replace("font-size:10px;", "font-size:12px;"))
    for label in row._wing_block_labels.values():
        label.setStyleSheet(label.styleSheet().replace("font-size:10px;", "font-size:11px;"))


def workspace_tabs(tabs):
    colors = theme.EDITOR_IVORY
    tabs.setObjectName("programmerWorkspaceTabs")
    tabs.tabBar().setObjectName("programmerWorkspaceTabBar")
    tabs.setStyleSheet(
        "QTabWidget#programmerWorkspaceTabs::pane{border:none;}"
        f"QTabBar#programmerWorkspaceTabBar::tab{{background:{colors['panel']};"
        f"color:{colors['muted']};border:none;padding:6px 12px;font-size:12px;}}"
        f"QTabBar#programmerWorkspaceTabBar::tab:selected{{"
        f"background:{colors['selected']};color:{colors['ink']};"
        f"border-bottom:2px solid {colors['accent']};}}"
        f"QTabBar#programmerWorkspaceTabBar::tab:focus{{color:{colors['focus']};}}")
