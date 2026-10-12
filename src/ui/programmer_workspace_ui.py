"""Programmer editor layout; domain state remains on the existing panel.

Uses Qt QSplitter/QScrollArea APIs (Qt v6.11.0, LGPL/commercial) and the
repository's BSD-3-Clause Qt FlowLayout adaptation. No upstream code copied.
Design/semantics: r185-programmer-redesign-adoption.md.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from . import programmer_highlight as highlight
from . import qascade_theme as theme
from .widget_kit import PanelSection, scope_qss
from .flow_layout import FlowLayout
from .main_icon_library import load_tabler_icon
from .programmer_inject import (
    _r183_build_ch_column, _r183_build_left_column, _r183_build_toolbar_extras,
)
from .programmer_panel_lifecycle import build_programmer_scroll
from .programmer_channel_ui import prepare_cue_grid
from .programmer_shared_state_ui import bind_cue_section
from . import programmer_workspace_style as style

log = logging.getLogger(__name__)


def _flow(parent=None):
    widget = QWidget(parent)
    layout = FlowLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    return widget, layout


def _label(text):
    label = QLabel(text)
    style.ivory_label(label)
    return label


def _horizontal():
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    return widget, layout


def _button(panel, text, slot, *, icon="", color=None):
    colors = theme.EDITOR_IVORY
    button = panel._mk_btn(text, colors["surface"], color or colors["ink"],
                           icon_key=icon)
    button.clicked.connect(slot)
    style.ivory_button(button, danger=text == "Release")
    return button


def _output_controls(panel):
    widget, layout = _flow()
    layout.addWidget(_label("临时编辑"))
    full = _button(panel, "Full", lambda: panel._ch_list.full_open(), icon="track_add")
    full.setToolTip("当前通道全部设为 255")
    layout.addWidget(full)
    release = _button(panel, "Release", panel.clear_live_output,
                      icon="clear_output", color=theme.BRIGHT_ERROR)
    release.setToolTip("停止当前 Programmer 输出，保留推杆/摇杆/颜色等编辑值\n"
                       "右键：释放选中通道 / 选中设备 / 全部")
    release.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    release.customContextMenuRequested.connect(
        lambda pos: highlight.show_release_menu(panel, release, pos))
    panel._release_btn = release
    layout.addWidget(release)
    _r183_build_toolbar_extras(panel, layout)
    style.ivory_button(panel._highlight_btn)
    style.ivory_label(panel._step_label)
    _finish_step_buttons(widget)
    return widget


def _finish_step_buttons(widget):
    for button in widget.findChildren(QPushButton):
        if button.text() not in ("◀", "▶"):
            continue
        previous = button.text() == "◀"
        button.setText("")
        button.setIcon(load_tabler_icon("chevron-left" if previous else "chevron-right",
                                       16, theme.EDITOR_IVORY["ink"]))
        button.setFixedSize(24, 24)
        style.ivory_button(button, compact=True)
        button.setAccessibleName("上一台设备" if previous else "下一台设备")


def _smoothing(panel, presets):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(_label("平滑"))
    panel._smooth_combo = QComboBox()
    panel._smooth_combo.setFixedHeight(24)
    panel._smooth_combo.setMinimumWidth(128)
    panel._smooth_combo.setToolTip("输出平滑预设：旁通=不处理，其余为内置平滑曲线")
    for key in ["bypass"] + [key for key in presets if key != "bypass"]:
        preset = presets.get(key)
        if preset is not None:
            panel._smooth_combo.addItem(preset.name, key)
    if not panel._smooth_combo.count():
        panel._smooth_combo.addItem("旁通(无处理)", "bypass")
    panel._smooth_combo.currentIndexChanged.connect(panel._on_smooth_preset_changed)
    style.ivory_field(panel._smooth_combo)
    layout.addWidget(panel._smooth_combo)
    return widget


def _selection_tools(panel):
    widget, layout = _horizontal()
    layout.addWidget(_label("选择 / 布局"))
    colors = theme.EDITOR_IVORY
    panel._pkg_arrange_btn = panel._mk_btn("包排列", colors["surface"], colors["ink"])
    panel._pkg_arrange_btn.setCheckable(True)
    panel._pkg_arrange_btn.toggled.connect(panel._on_pkg_arrange_toggled)
    panel._pkg_arrange_btn.setToolTip("仅整理包内子设备的显示，不改动任何通道值")
    style.ivory_button(panel._pkg_arrange_btn)
    layout.addWidget(panel._pkg_arrange_btn)
    refresh = _button(panel, "刷新", panel._refresh_fixtures, icon="layout_reset")
    refresh.setToolTip("刷新设备列表")
    layout.addWidget(refresh)
    sync = _button(panel, "TopView同步", panel.sync_from_topview_selection, icon="topview")
    sync.setToolTip("重新载入 TopView 当前选中设备")
    layout.addWidget(sync)
    return widget


def _binding_tools(panel):
    widget, layout = _horizontal()
    layout.addWidget(_label("外接摇杆"))
    colors = theme.EDITOR_IVORY
    panel._joy_bind_btn = panel._mk_btn("绑定到摇杆", colors["surface"],
                                       colors["ink"], icon_key="joystick_device")
    panel._joy_bind_btn.setCheckable(True)
    panel._joy_bind_btn.setToolTip("进入绑定模式：悬停控件高亮，点击控件绑定摇杆轴")
    panel._joy_bind_btn.toggled.connect(panel._toggle_joystick_binding_mode)
    style.ivory_button(panel._joy_bind_btn)
    layout.addWidget(panel._joy_bind_btn)
    panel._joy_mgr_btn = _button(panel, "管理绑定…", panel._open_binding_manager,
                                icon="joystick_device")
    panel._joy_mgr_btn.setToolTip("查看和管理所有已绑定控件与摇杆轴映射")
    layout.addWidget(panel._joy_mgr_btn)
    return widget


class ToolShelfController(QObject):
    """Owned UI-only shelf; a live binding mode keeps its exit control reachable."""

    def __init__(self, toggle, shelf, binding):
        super().__init__(toggle)
        self._toggle, self._shelf, self._binding = toggle, shelf, binding
        toggle.toggled.connect(self._set_expanded)
        binding.toggled.connect(self._binding_changed)
        shelf.setVisible(toggle.isChecked())

    @pyqtSlot(bool)
    def _set_expanded(self, expanded):
        if not expanded and self._binding.isChecked():
            self._toggle.setChecked(True)
            return
        self._shelf.setVisible(expanded)

    @pyqtSlot(bool)
    def _binding_changed(self, checked):
        if checked:
            self._toggle.setChecked(True)


def _tools_shelf(panel, parent, presets):
    toggle = QPushButton("工具与外设", parent)
    toggle.setCheckable(True)
    toggle.setFixedHeight(26)
    toggle.setToolTip("布局/同步、输出平滑与外接摇杆；绑定期间保持退出入口可见")
    style.ivory_button(toggle)
    shelf, layout = _flow(parent)
    layout.setSpacing(12)
    layout.addWidget(_selection_tools(panel))
    layout.addWidget(_smoothing(panel, presets))
    layout.addWidget(_binding_tools(panel))
    toggle._shelf_controller = ToolShelfController(toggle, shelf, panel._joy_bind_btn)
    panel._tools_toggle, panel._tools_shelf = toggle, shelf
    return toggle, shelf


def build_toolbar(panel, presets):
    bar = QWidget(panel)
    style.ivory_surface(bar)
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(12, 8, 12, 4)
    layout.setSpacing(4)
    header, header_layout = _horizontal()
    title = QLabel("Programmer")
    style.ivory_label(title, title=True)
    title.setToolTip("设备组选择、真实通道参数与临时输出编辑")
    header_layout.addWidget(title)
    header_layout.setSpacing(12)
    header_layout.addWidget(_output_controls(panel), 1)
    toggle, shelf = _tools_shelf(panel, bar, presets)
    header_layout.addWidget(toggle)
    layout.addWidget(header)
    layout.addWidget(shelf)
    return bar


def _curtain(panel):
    button = QPushButton("展开Programmer主体", panel)
    button.setCheckable(True)
    button.setChecked(True)
    button.setFixedHeight(24)
    button.setToolTip("折叠/展开操作台主体（设备组与通道条带区）")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    colors = theme.EDITOR_IVORY
    button.setStyleSheet(
        f"QPushButton{{background:{colors['panel']};color:{colors['muted']};"
        f"border:none;border-bottom:1px solid {colors['border']};"
        "border-radius:0;text-align:left;padding:0 8px;font-size:11px;min-height:0px;}"
        f"QPushButton:hover{{background:{colors['hover']};}}")
    button.toggled.connect(panel._toggle_programmer_curtain)
    panel._curtain_btn = button
    return button


def _channel_column(panel):
    column = _r183_build_ch_column(panel)
    bar = panel._post_filter_bar
    original = bar.layout()
    wrap, flow = _flow()
    widgets = []
    while original.count():
        widget = original.takeAt(0).widget()
        if widget is not None:
            widget.setMinimumHeight(24)
            widget.setMaximumHeight(24)
            widgets.append(widget)
    widgets[0].setText("当前选择内过滤")
    for widget in widgets:
        style.ivory_control(widget)
    address, address_layout = _horizontal()
    for widget in widgets[3:7]:
        address_layout.addWidget(widget)
    for widget in widgets[:3] + [address] + widgets[7:]:
        flow.addWidget(widget)
    original.setContentsMargins(8, 4, 8, 4)
    original.addWidget(wrap)
    bar.setMinimumHeight(32)
    style.ivory_surface(bar)
    return column


def _object_column(panel):
    column = _r183_build_left_column(panel)
    style.object_column(column, panel._group_selector)
    for bar in (panel._palette_bar, panel._effect_quick_bar):
        if bar is not None:
            style.ivory_surface(bar)
            for widget in bar.findChildren(QWidget):
                style.ivory_control(widget)
    if panel._palette_bar is not None:
        _wrap_palette(panel._palette_bar)
    if panel._effect_quick_bar is not None:
        _scroll_quick_bar(column, panel._effect_quick_bar)
    return column


def _wrap_palette(bar):
    original = bar.layout()
    wrapper, flow = _flow(bar)
    while original.count():
        widget = original.takeAt(0).widget()
        if widget is not None:
            flow.addWidget(widget)
    original.addWidget(wrapper)


def _scroll_quick_bar(column, bar):
    column.layout().removeWidget(bar)
    scroll = QScrollArea(column)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setMinimumWidth(0)
    scroll.setFixedHeight(52)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    style.ivory_scroll(scroll)
    scroll.setWidget(bar)
    column.layout().addWidget(scroll)


def _main_body(panel, group_factory, channel_factory, cue_factory):
    panel._main_body_widget = QWidget()
    layout = QVBoxLayout(panel._main_body_widget)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)
    splitter = QSplitter(Qt.Orientation.Horizontal, panel._main_body_widget)
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(4)
    panel._group_selector = group_factory()
    panel._group_selector.setMinimumWidth(160)
    panel._group_selector.setMaximumWidth(288)
    panel._group_selector.group_selected.connect(panel._on_group_selected)
    panel._group_selector.group_delete_requested.connect(panel._delete_fixture_group_from_programmer)
    panel._group_selector.logical_mode_toggle_requested.connect(panel._on_logical_mode_toggle)
    panel._group_selector.light_control_mode_changed.connect(panel._on_light_control_mode_changed)
    splitter.addWidget(_object_column(panel))
    panel._ch_list = channel_factory()
    panel._ch_list._panel = panel
    for signal, slot in (
        ("layer_value_changed", "_on_layer_value_changed"),
        ("layer_batch_changed", "_on_layer_batch_changed"),
        ("value_changed", "_on_value_changed"),
        ("joy_amplitude_changed", "_on_joy_amplitude_changed"),
        ("sync_z_command_changed", "_on_sync_z_command"),
        ("controlTrajectoryEvent", "controlTrajectoryEvent"),
        ("status_feedback", "status_feedback"),
    ):
        getattr(panel._ch_list, signal).connect(getattr(panel, slot))
    splitter.addWidget(_channel_column(panel))
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([208, 860])
    panel._workspace_splitter = splitter
    layout.addWidget(splitter, 1)
    panel._cue_grid = cue_factory()
    prepare_cue_grid(panel._cue_grid)
    panel._cue_grid.cue_selected.connect(panel._on_cue_selected)
    panel._cue_grid.cue_send_to_timeline.connect(panel._send_cue_to_timeline)
    section = PanelSection("自映射 Cue", expanded=bool(panel._cue_grid.get_cues()),
                           parent=panel._main_body_widget, icon_key="playlist")
    section.add_widget(panel._cue_grid)
    panel._cue_section = section
    bind_cue_section(panel._cue_grid, section)
    layout.addWidget(section)
    panel._main_body_widget.setMinimumSize(0, 0)
    panel._programmer_scroll = build_programmer_scroll(panel)


def build_workspace(panel, group_factory, channel_factory, cue_factory):
    scope_qss(panel, f"background:{theme.BG_ROOT};")
    panel.setMinimumSize(0, 0)
    panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(panel._build_toolbar())
    layout.addWidget(_curtain(panel))
    _main_body(panel, group_factory, channel_factory, cue_factory)
    panel._sub_tabs = QTabWidget(panel)
    style.workspace_tabs(panel._sub_tabs)
    panel._sub_tabs.addTab(panel._programmer_scroll, "操作台")
    try:
        panel._sub_tabs.addTab(panel._build_trajectory_page(), "轨迹曲线")
    except Exception as exc:
        log.warning("ProgrammerPanel 轨迹曲线页构建失败: %s", exc)
    layout.addWidget(panel._sub_tabs, 1)
