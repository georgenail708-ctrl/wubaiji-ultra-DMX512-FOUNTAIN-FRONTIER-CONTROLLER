"""Rearrange the original Effects editor through native Qt regions.

References: Qt QListView flow/wrapping and QSplitter APIs,
Asset-browser style panel layout.
All original widgets, model indexes, controls and business signals survive.
"""
from __future__ import annotations

import weakref

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import effect_workspace_style as style
from . import qascade_theme as theme


def install(panel):
    page = panel._subpage_stack.widget(0)
    root = page.layout()
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)
    root.removeWidget(panel._cat_bar)
    tools = root.itemAt(1).widget()
    root.removeWidget(tools)
    _tool_shelf(panel._effect_list_panel, tools)
    split = panel._main_split
    selection = split.widget(0)
    sidebar = _sidebar(panel)
    split.insertWidget(0, sidebar)
    split.insertWidget(1, panel._effect_list_panel)
    selection.setParent(page)
    selection.hide()
    selection.deleteLater()
    panel._effect_adjust_panel.setMinimumWidth(256)
    panel._param_channel_frame.setMinimumWidth(0)
    panel._effect_list_panel.setMinimumWidth(192)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setStretchFactor(2, 0)
    split.setSizes([180, 700, 420])
    panel._param_body_split.setOrientation(Qt.Orientation.Vertical)
    for index in range(panel._param_body_split.count()):
        panel._param_body_split.widget(index).setMinimumHeight(96)
    _flow_control_rows(panel)
    _identity_field(panel)
    panel._effect_surface_context = _SurfaceContext(panel)
    style.decorate_shell(panel)
    _setup_card_view(panel._preset_list)


def _flow_control_rows(panel):
    from PyQt6.QtWidgets import QComboBox, QHBoxLayout
    from .flow_layout import FlowLayout

    grid = panel._param_container.parentWidget().layout()
    for index in range(grid.count() - 1, -1, -1):
        old = grid.itemAt(index).layout()
        if not isinstance(old, QHBoxLayout):
            continue
        is_transport = old.indexOf(panel._play_btn) >= 0
        row, column, rows, columns = grid.getItemPosition(index)
        grid.removeItem(old)
        flow = FlowLayout()
        flow.setSpacing(old.spacing())
        while old.count():
            flow.addItem(old.takeAt(0))
        old.setParent(panel)
        old.deleteLater()
        if is_transport:
            _attach_transport(panel, flow)
        else:
            grid.addLayout(flow, row, column, rows, columns)
    for combo in panel._param_container.parentWidget().findChildren(QComboBox):
        combo.setMinimumContentsLength(10)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)


def _attach_transport(panel, flow):
    host = QWidget(panel._effect_adjust_panel)
    host.setLayout(flow)
    outer = panel._effect_adjust_panel.layout()
    outer.insertWidget(outer.count() - 2, host)
    panel._effect_transport = host


def _setup_card_view(view):
    from PyQt6.QtWidgets import QListView

    view.setViewMode(QListView.ViewMode.IconMode)
    view.setFlow(QListView.Flow.LeftToRight)
    view.setWrapping(True)
    view.setMovement(QListView.Movement.Static)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setLayoutMode(QListView.LayoutMode.Batched)
    view.setBatchSize(64)
    view.setUniformItemSizes(False)
    view.setSpacing(0)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setAccessibleName("效果库：选择调参，星标收藏，右键预设操作")


def _identity_field(panel):
    label = QLabel(panel._effect_adjust_panel)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(True)
    label.setFont(theme.cached_font(11, families=theme.MONO_FONT_FAMILIES))
    label.setStyleSheet(f'color:{theme.EDITOR_IVORY["muted"]};background:transparent;')
    panel._effect_adjust_panel.layout().insertWidget(1, label)
    panel._effect_identity = label


def _sidebar(panel):
    from .effect_category_navigation import install as install_navigation

    box = QWidget()
    box.setMinimumWidth(128)
    box.setMaximumWidth(224)
    box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    install_navigation(panel._cat_bar)
    layout.addWidget(panel._cat_bar, 1)
    quick = panel._effect_quick_panel
    quick.setMinimumWidth(0)
    quick.setMaximumWidth(16777215)
    layout.addWidget(quick, 0)
    panel._effect_navigation = box
    return box


def _tool_shelf(library, tools):
    row = QToolButton(library)
    row.setText("工具与浏览选项")
    row.setCheckable(True)
    row.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    row.setArrowType(Qt.ArrowType.RightArrow)
    row.setAccessibleName("展开原效果库工具与浏览选项")
    tools.setParent(library)
    tools.hide()
    row.toggled.connect(tools.setVisible)
    row.toggled.connect(lambda open_: row.setArrowType(
        Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow))
    library.layout().insertWidget(0, tools)
    library.layout().insertWidget(0, row)
    library._effect_tool_shelf = row


class _SurfaceContext(QObject):
    """Resize only the original parameter split, no polling or new renderer."""

    def __init__(self, panel):
        super().__init__(panel._search)
        self._panel = weakref.ref(panel)
        self._vertical = True
        self._stacked = None
        panel._effect_adjust_panel.installEventFilter(self)
        for view in (panel._active_effect_list, panel._history_effect_list):
            for signal in (view.model().rowsInserted, view.model().rowsRemoved,
                           view.model().modelReset):
                signal.connect(self.resize_quick_lists)
        self.resize_quick_lists()

    @pyqtSlot()
    def resize_quick_lists(self):
        panel = self._panel()
        if panel is None:
            return
        height = 0
        for view in (panel._active_effect_list, panel._history_effect_list):
            rows = min(3, view.count())
            size = sum(view.item(row).sizeHint().height() + 2 for row in range(rows))
            view.setMinimumHeight(0)
            view.setMaximumHeight(size + 6 if rows else 0)
            height += view.maximumHeight()
        panel._effect_quick_panel.setMaximumHeight(height + 56)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            panel = self._panel()
            if panel is not None:
                self.arrange_columns(panel, watched.width())
                vertical = watched.width() < 760
                if vertical != self._vertical:
                    orientation = (Qt.Orientation.Vertical if vertical
                                   else Qt.Orientation.Horizontal)
                    panel._param_body_split.setOrientation(orientation)
                    self._vertical = vertical
        return False

    def arrange_columns(self, panel, width):
        stacked = width < 660
        if stacked == self._stacked:
            return
        layout = panel._param_lay
        column = panel._param_col_right
        layout.removeItem(column)
        column.setParent(None)
        layout.addLayout(column, 1 if stacked else 0, 0 if stacked else 1)
        layout.setColumnStretch(1, 0 if stacked else 1)
        self._stacked = stacked


def refresh_parameters(panel):
    if hasattr(panel, "_effect_surface_context"):
        style.decorate_parameters(panel)
        panel._effect_surface_context.arrange_columns(panel, panel._effect_adjust_panel.width())
        panel._effect_identity.setText(str(panel._selected_id or ""))


def refresh_transport(panel):
    if hasattr(panel, "_effect_surface_context"):
        style.decorate_transport_state(panel)
