"""Native presentation adapters for existing Programmer controls.

Qt v6.11 QScrollArea/QSplitter APIs; existing BSD-3-Clause FlowLayout and
MIT Tabler icons. No value mapping, controller model or producer is created.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractSlider, QFrame, QHBoxLayout, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)

from . import qascade_theme as theme
from .widget_kit import scope_qss
from .flow_layout import FlowLayout
from .main_icon_library import load_tabler_icon
from .project_channels import CH_LABELS
from .slider_input_keys import reserve_slider_keys
from . import programmer_workspace_style as style


def make_group_row(name, color, count, *, mode=""):
    button = QPushButton()
    button.setMinimumSize(64, 44)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    suffix = f" · {mode}" if mode else ""
    button.setText(f"{name}\n{count} 台{suffix}")
    button.setStyleSheet(style.group_row_qss(color))
    return button


def arrange_group_rows(selector):
    layout = selector._btn_lay
    widgets = []
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget is not None:
            widgets.append(widget)
    layout.setSpacing(4)
    layout.setColumnStretch(0, 1)
    layout.setColumnStretch(1, 0)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, index, 0, 1, 2)


def style_channel_header(row, available, color):
    background = theme.BG_EDITOR if available else theme.BG_BOX
    scope_qss(row._header,
              f"background:{background};border-bottom:1px solid {theme.BORDER_NORMAL};"
              f"border-left:3px solid {color};")


def _flow_widget(widgets, parent=None):
    wrapper = QWidget(parent)
    layout = FlowLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for widget in widgets:
        layout.addWidget(widget)
    return wrapper


def _take_widgets(layout):
    widgets = []
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget is not None:
            widgets.append(widget)
    return widgets


def _field_group(label, control):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(label)
    layout.addWidget(control)
    return widget


def _parameter_groups(row, widgets):
    pairs = {row._header_wing_label: row._header_wing_spin,
             row._header_block_label: row._header_block_spin}
    grouped = []
    for widget in widgets:
        if widget in pairs.values():
            continue
        if widget in pairs:
            grouped.append(_field_group(widget, pairs[widget]))
        else:
            grouped.append(widget)
    return grouped


def _identity_widgets(row, widgets):
    boundary = 2 + 2 * len(row._ch_types)
    row._title.setMinimumWidth(96)
    row._title.setMaximumWidth(16777215)
    for index, key in enumerate(row._ch_types):
        badge = widgets[2 + index]
        badge.setText(CH_LABELS.get(key, key))
        badge.setMinimumWidth(32)
        badge.setMaximumWidth(16777215)
    return widgets[:boundary], widgets[boundary:]


def _arrangement_controls(row, widgets):
    secondary = set(row._responsive_secondary_widgets)
    details = [widget for widget in widgets if widget in secondary]
    main = [widget for widget in widgets if widget not in secondary]
    row._jitter_label.setText("共享抖动%")
    shared_hint = "Programmer共享抖动；此处沿原面板值，不是本族独立参数"
    row._jitter_label.setToolTip(shared_hint)
    row._jitter_spin.setToolTip(shared_hint + "\n" + row._jitter_spin.toolTip())
    for button in details:
        if isinstance(button, QPushButton) and button.text() == "按位置排序":
            button.setText("当前组按位置排序")
    return main, _parameter_groups(row, details)


def _details_button(row, details):
    button = QPushButton("编排", row._header)
    button.setObjectName("programmerArrangementToggle")
    button.setCheckable(True)
    button.setFixedHeight(24)
    button.setIcon(load_tabler_icon("adjustments-horizontal", 16, theme.TEXT_PRIMARY))
    button.setToolTip("展开原翼/块/六方向、当前组排序与Programmer共享抖动；不改变值")
    button.toggled.connect(details.setVisible)
    details.setVisible(False)
    row._arrangement_toggle = button
    row._arrangement_details = details
    return button


def arrange_channel_header(row, color):
    original = row._header.layout()
    identity, remainder = _identity_widgets(row, _take_widgets(original))
    main, arrangement = _arrangement_controls(row, remainder)
    row._header.setMinimumHeight(0)
    row._header.setMaximumHeight(16777215)
    content = QWidget(row._header)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    details = _flow_widget(arrangement, content)
    toggle = _details_button(row, details)
    identity.extend((toggle, row._ctrl_btn))
    layout.addWidget(_flow_widget(identity, content))
    layout.addWidget(_flow_widget([widget for widget in main if widget is not row._ctrl_btn], content))
    layout.addWidget(details)
    original.setContentsMargins(8, 4, 8, 4)
    original.addWidget(content)
    row._ctrl_btn.setIcon(load_tabler_icon("adjustments", 16, theme.TEXT_PRIMARY))
    row._ctrl_btn.setFixedWidth(88)
    if hasattr(row, "_master_slider"):
        reserve_slider_keys(row._master_slider)
    style.dark_labels(row)
    style_channel_header(row, True, color)


def _scroll_page(page):
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setMinimumSize(0, 0)
    scroll.setWidget(page)
    return scroll


def _joystick_columns(page):
    original = page.layout()
    info = original.takeAt(0).layout()
    joystick = original.takeAt(0).widget()
    strips = original.takeAt(0).widget()
    properties = QWidget()
    info.setParent(None)
    properties.setLayout(info)
    properties.setMinimumWidth(200)
    control = QWidget()
    layout = QVBoxLayout(control)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(8)
    layout.addWidget(joystick, 0, Qt.AlignmentFlag.AlignHCenter)
    layout.addWidget(strips)
    layout.addStretch()
    splitter = QSplitter(Qt.Orientation.Horizontal, page)
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(4)
    splitter.addWidget(properties)
    splitter.addWidget(control)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([280, 480])
    original.setContentsMargins(4, 4, 4, 4)
    original.addWidget(splitter)


def finish_controller_ui(row, panel, tabs):
    # Called only by the existing lazy factory. All original widgets survive.
    layout = panel.layout()
    title_layout = layout.itemAt(0).layout()
    widgets = _take_widgets(title_layout)
    title_layout.addWidget(_flow_widget(widgets))
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)
    definition = row._fixture_defs.get(row._def_id)
    context = f"{getattr(definition, 'name', '')} · {row._group_name}".strip(" ·")
    row._controller_title.setToolTip(context)
    panel.setAccessibleName(f"{context} 控制器")
    current = tabs.currentIndex()
    for index in range(tabs.count()):
        page = tabs.widget(index)
        text = tabs.tabText(index)
        if text == "摇杆":
            _joystick_columns(page)
        tabs.removeTab(index)
        scroll = _scroll_page(page)
        scroll.setAccessibleName(f"{context} · {text}")
        tabs.insertTab(index, scroll, text)
    tabs.setCurrentIndex(current)
    tabs.setMinimumHeight(240)
    layout.addWidget(tabs)
    row._controller_tabs = tabs
    for slider in panel.findChildren(QAbstractSlider):
        reserve_slider_keys(slider)
    return panel


def finish_trajectory_ui(body, splitter):
    body.layout().setContentsMargins(4, 4, 4, 4)
    splitter.setHandleWidth(4)
    # Keep the existing three-column editing and small-dock scroll contract.
    for index in range(splitter.count()):
        widget = splitter.widget(index)
        widget.layout().setContentsMargins(8, 8, 8, 8)
        widget.layout().setSpacing(8)
    splitter.setSizes([200, 700, 280])


def prepare_cue_grid(grid):
    grid.setFixedHeight(112)
    title_layout = grid.layout().itemAt(0).layout()
    widgets = _take_widgets(title_layout)
    # The PanelSection now owns the title; retain the existing menu hint.
    widgets[0].setVisible(False)
    title_layout.addWidget(_flow_widget(widgets[1:]))
