# -*- coding: utf-8 -*-
"""M01 editor assembly; the existing panel owns every command and engine.

References: R185 Matrix concept A / Matrix / 编辑器分区.
FlowLayout is the repository adaptation of Qt 6.11's BSD-3-Clause example;
see r185-matrix-redesign-adoption.md for the fixed upstream source and licence.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QMenu,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import qascade_theme as theme
from ..widget_kit import PanelSection, prop_row, scope_qss
from ..flow_layout import FlowLayout
from ..main_icon_library import make_main_icon
from . import matrix_w2_ui
from .matrix_channel_profile_ui import build_profile_control


def _host():
    from . import matrix_slideshow_panel
    return matrix_slideshow_panel


def _label(text: str, *, secondary: bool = True) -> QLabel:
    label = QLabel(text)
    colour = theme.TEXT_SECONDARY if secondary else theme.TEXT_PRIMARY
    label.setStyleSheet(f"color:{colour};font-size:{theme.FS_SMALL}px;")
    return label


def _row(*widgets) -> QWidget:
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for widget in widgets:
        row.addWidget(widget)
    return host


def _flow_host(name: str) -> tuple:
    host = QFrame()
    host.setObjectName(name)
    scope_qss(host, f"background:{theme.BG_EDITOR};border-radius:4px;")
    flow = FlowLayout(host)
    flow.setContentsMargins(8, 8, 8, 8)
    flow.setSpacing(8)
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    policy.setHeightForWidth(True)
    host.setSizePolicy(policy)
    return host, flow


def build_main_workspace(panel) -> QWidget:
    """Reassemble one preview with local properties and wrapping transport."""
    zone = QWidget()
    zone.setObjectName("matrixMainWorkspace")
    layout = QVBoxLayout(zone)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    layout.addWidget(_build_context_header(panel))
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.setHandleWidth(4)
    split.addWidget(panel._build_canvas_panel())
    split.addWidget(_build_properties(panel))
    split.setCollapsible(1, True)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 0)
    split.setSizes([960, 248])
    panel._matrix_workspace_splitter = split
    layout.addWidget(split, 1)
    layout.addWidget(_build_transport(panel))
    return zone


def _build_context_header(panel) -> QWidget:
    host, flow = _flow_host("matrixContextHeader")
    title = _label("Matrix", secondary=False)
    font = title.font()
    font.setBold(True)
    title.setFont(font)
    flow.addWidget(title)
    panel._filter_group_combo = QComboBox()
    panel._filter_group_combo.setFixedWidth(160)
    panel._filter_group_combo.addItem("全部设备", None)
    panel._filter_group_combo.currentIndexChanged.connect(panel._on_filter_group_changed)
    flow.addWidget(_row(_label("设备组"), panel._filter_group_combo))
    panel._group_lbl = _label("未选择设备组", secondary=False)
    panel._group_lbl.setWordWrap(True)
    panel._group_lbl.setMaximumWidth(196)
    panel._dev_lbl = _label("设备: 0")
    flow.addWidget(_row(panel._group_lbl, panel._dev_lbl))
    panel._ch_type_combo = QComboBox()
    panel._ch_type_combo.setFixedWidth(144)
    panel._ch_type_combo.currentIndexChanged.connect(panel._on_ch_type_changed)
    panel._populate_ch_type_combo()
    flow.addWidget(_row(_label("通道"), panel._ch_type_combo))
    flow.addWidget(_row(_label("映射"), build_profile_control(panel)))
    flow.addWidget(_build_editor_entries(panel))
    return host


def _build_editor_entries(panel) -> QWidget:
    panel._ppt_mode_btn = panel._mk_btn(
        "幻灯片", theme.BG_BUTTON, theme.TEXT_PRIMARY, icon_key="presentation")
    panel._ppt_mode_btn.setCheckable(True)
    panel._ppt_mode_btn.setChecked(True)
    panel._ppt_mode_btn.setToolTip("选择幻灯片模式并打开/置前独立编辑窗口")
    panel._ppt_mode_btn.toggled.connect(panel._on_ppt_mode_toggled)
    panel._ppt_mode_btn.clicked.connect(panel._on_ppt_btn_clicked)
    panel._pixel_mode_btn = panel._mk_btn(
        "像素效果", theme.BG_BUTTON, theme.TEXT_PRIMARY, icon_key="grid-dots")
    panel._pixel_mode_btn.setCheckable(True)
    panel._pixel_mode_btn.setToolTip("选择像素模式并打开/置前独立编辑窗口")
    panel._pixel_mode_btn.toggled.connect(panel._on_pixel_mode_toggled)
    panel._pixel_mode_btn.clicked.connect(panel._on_pixel_btn_clicked)
    return _row(panel._ppt_mode_btn, panel._pixel_mode_btn)


def _view_spin(panel, field: str, spec: tuple, callback) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    minimum, maximum, step, tip = spec
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(2)
    spin.setValue(1.0)
    spin.setMinimumWidth(80)
    spin.setToolTip(tip)
    spin.valueChanged.connect(callback)
    setattr(panel, field, spin)
    return spin


def _build_view_controls(panel) -> None:
    _view_spin(panel, "_layout_width_spin", (0.25, 6.0, 0.25,
               "调节矩阵预览布局的横向展开比例。"),
               panel._set_layout_width_scale)
    _view_spin(panel, "_layout_height_spin", (0.25, 6.0, 0.25,
               "调节矩阵预览布局的纵向展开比例。"),
               panel._set_layout_height_scale)
    _view_spin(panel, "_preview_zoom_spin", (0.35, 2.50, 0.05,
               "缩放预览画布和设备点；不改变实际输出。"),
               panel._set_preview_zoom)
    panel._topview_compact_btn = QToolButton()
    panel._topview_compact_btn.setText("缩小")
    panel._topview_compact_btn.setCheckable(True)
    panel._topview_compact_btn.setToolTip("切换预览紧凑高度；内容仍可滚动浏览。")
    panel._topview_compact_btn.toggled.connect(panel._set_topview_compact)


def build_canvas_panel(panel) -> QWidget:
    """Keep the original canvas, signals and scroll/compact behaviour."""
    host = QWidget()
    host.setMinimumWidth(160)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    _build_view_controls(panel)
    section = PanelSection("设备排布 · 预览", icon_key="grid-dots")
    hint = _label("TopView 可滚动浏览 · ON/OFF 为当前预览值")
    hint.setWordWrap(True)
    section.add_widget(hint)
    panel._canvas = _host().FixtureCanvas()
    panel._canvas.selection_changed.connect(panel._on_canvas_changed)
    panel._canvas_scroll = QScrollArea()
    panel._canvas_scroll.setWidget(panel._canvas)
    panel._canvas_scroll.setWidgetResizable(False)
    panel._canvas_scroll.setMinimumWidth(128)
    panel._canvas_scroll.setMinimumHeight(200)
    panel._canvas_scroll.setMaximumHeight(16777215)
    panel._canvas_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    panel._canvas_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    panel._canvas_scroll.setFrameShape(QFrame.Shape.NoFrame)
    scope_qss(panel._canvas_scroll,
              f"background:{theme.BG_ROOT};border:1px solid {theme.BORDER_NORMAL};")
    section.add_widget(panel._canvas_scroll)
    layout.addWidget(section, 1)
    return host


def _build_properties(panel) -> QScrollArea:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(4, 0, 4, 4)
    layout.setSpacing(8)
    view = PanelSection("预览视图", icon_key="zoom_in")
    for label, widget in (("宽度比例", panel._layout_width_spin),
                          ("高度比例", panel._layout_height_spin),
                          ("缩放", panel._preview_zoom_spin),
                          ("紧凑高度", panel._topview_compact_btn)):
        view.add_widget(prop_row(label, widget, label_width=64))
    layout.addWidget(view)
    layout.addWidget(_build_slice_section(panel))
    tip = _label("视图比例与缩放只影响预览。\n帧编辑在幻灯片独立窗口，媒体与像素参数在像素效果独立窗口。")
    tip.setWordWrap(True)
    layout.addWidget(tip)
    layout.addStretch()
    scroll = QScrollArea()
    scroll.setObjectName("matrixLocalProperties")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setMinimumWidth(240)
    scroll.setMaximumWidth(320)
    scope_qss(scroll, f"background:{theme.BG_ROOT};border:none;")
    scroll.setWidget(host)
    return scroll


def _build_slice_section(panel) -> PanelSection:
    section = PanelSection("切片键", icon_key="joystick_device")
    panel._slice_key_lbl = _label("未绑定", secondary=False)
    panel._slice_key_lbl.setWordWrap(True)
    panel._slice_key_lbl.setAccessibleName("切片键绑定状态")
    section.add_widget(panel._slice_key_lbl)
    panel._slice_key_bind_btn = panel._mk_btn(
        "绑定", theme.BG_BUTTON, theme.TEXT_PRIMARY, icon_key="joystick_device")
    panel._slice_key_bind_btn.clicked.connect(panel._begin_slice_key_capture)
    panel._slice_key_clear_btn = panel._mk_btn(
        "清除", theme.BG_BUTTON, theme.BRIGHT_ERROR, icon_key="clear_output")
    panel._slice_key_clear_btn.clicked.connect(panel._clear_slice_key_binding)
    section.add_widget(_row(panel._slice_key_bind_btn, panel._slice_key_clear_btn))
    return section


def _build_transport(panel) -> QWidget:
    host, flow = _flow_host("matrixTransportFooter")
    playback, settings = _build_playback_controls(panel)
    flow.addWidget(playback)
    flow.addWidget(settings)
    record = matrix_w2_ui.build_record_controls(panel)
    panel._record_btn.setIcon(make_main_icon("record", 16))
    panel._record_state_lbl.setWordWrap(True)
    panel._record_state_lbl.setMaximumWidth(128)
    panel._record_state_lbl.setAccessibleName("矩阵输出录制状态")
    flow.addWidget(record)
    flow.addWidget(_build_output_menu(panel))
    return host


def _build_playback_controls(panel) -> tuple:
    panel._play_btn = panel._mk_btn(
        "播放", theme.BG_BUTTON, theme.TEXT_PRIMARY, icon_key="play")
    panel._play_btn.setCheckable(True)
    panel._play_btn.toggled.connect(panel._on_play_toggled)
    stop = panel._mk_btn("", theme.BG_BUTTON, theme.BRIGHT_ERROR, icon_key="stop")
    stop.setFixedWidth(40)
    stop.setToolTip("停止矩阵输出")
    stop.setAccessibleName("停止矩阵输出")
    stop.clicked.connect(panel._stop)
    previous = panel._mk_btn("", theme.BG_BUTTON, theme.TEXT_SECONDARY,
                             icon_key="skip_back")
    previous.setFixedWidth(40)
    previous.setToolTip("上一帧 / 上一切片")
    previous.clicked.connect(lambda: panel._step_frame(-1))
    following = panel._mk_btn("", theme.BG_BUTTON, theme.TEXT_SECONDARY,
                              icon_key="skip_forward")
    following.setFixedWidth(40)
    following.setToolTip("下一帧 / 下一切片")
    following.clicked.connect(lambda: panel._step_frame(1))
    panel._play_mode_combo = QComboBox()
    for key, label in _host().PLAY_MODES.items():
        panel._play_mode_combo.addItem(label, key)
    panel._play_mode_combo.setFixedWidth(64)
    panel._play_mode_combo.currentIndexChanged.connect(panel._on_play_mode_changed)
    panel._fps_spin = QSpinBox()
    panel._fps_spin.setRange(1, 60)
    panel._fps_spin.setValue(25)
    panel._fps_spin.setFixedWidth(52)
    panel._fps_spin.setToolTip("矩阵播放的配置刷新率（帧/秒）。")
    panel._fps_spin.valueChanged.connect(panel._on_matrix_fps_changed)
    playback = _row(panel._play_btn, stop, previous, following)
    settings = _row(_label("模式"), panel._play_mode_combo,
                    _label("刷新率"), panel._fps_spin)
    return playback, settings


def _build_output_menu(panel) -> QToolButton:
    """Low-frequency original entry points; no new output command."""
    button = QToolButton()
    button.setText("输出工具")
    button.setIcon(make_main_icon("settings", 16))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setToolTip("输出平滑、分区Gamma、电平侧窗、串口固件桥接说明与帮助")
    menu = QMenu(button)
    _menu_action(menu, "输出平滑…", panel._open_output_shape_dialog)
    _menu_action(menu, "分区 Gamma…", panel._open_output_gamma_dialog)
    menu.addSeparator()
    _menu_action(menu, "输出电平…", lambda: matrix_w2_ui.open_level_window_for(panel))
    _menu_action(menu, "串口桥接说明…", lambda: matrix_w2_ui.open_serial_bridge_help(panel))
    menu.addSeparator()
    _menu_action(menu, "矩阵帮助…", panel._show_help)
    button.setMenu(menu)
    return button


def _menu_action(menu: QMenu, label: str, callback) -> None:
    action = menu.addAction(label)
    action.triggered.connect(callback)
