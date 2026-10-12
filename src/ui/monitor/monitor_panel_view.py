# -*- coding: utf-8 -*-
"""MonitorPanel 扩展区装配（R183 W2；深查② §四 #3/#7/#8/#11/#14/#16）。

从 monitor_panel.py 拆出（文件 ≤500 行门禁）：选中行（迷你电平条 + 统计）、
右栏扩展区（sparkline / 录制回放条 / 导出钮 / 动作反馈行）、左栏输入输出
对照条。装配函数只建控件并挂到 ``panel`` 属性，行为在
``monitor_panel_extras.MonitorPanelExtrasMixin``。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt

from ..widget_kit import PanelSection
from ..qascade_theme import SEP_LINE, TEXT_SECONDARY
from .monitor_io_compare import IoCompareView
from .monitor_sparkline import ChannelSparkline
from .monitor_vu import LevelBar
from .monitor_timeline_slider import MonitorTimelineSlider

_EXTRAS_BTN_QSS = (
    "QPushButton{background:#303030;color:#e6e6e6;border:1px solid #555;"
    "border-radius:3px;font-size:11px;padding:0 8px;}"
    "QPushButton:checked{background:#4772b3;color:#ffffff;border-color:#71a8ff;}"
    "QPushButton:disabled{color:#7a7a7a;border-color:#444;}")


def _caption(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
    return lbl


def make_extras_button(text: str, tip: str) -> QPushButton:
    """右栏小按钮（与热图切换同风格；checkable 由调用方按需开）。"""
    btn = QPushButton(text)
    btn.setFixedHeight(24)
    btn.setToolTip(tip)
    btn.setStyleSheet(_EXTRAS_BTN_QSS)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def build_selection_row(panel) -> QWidget:
    """选中行：迷你电平条 + 统计文本（R183 W2 #3/#11）。

    返回 QQWidget；``panel._vu`` / ``panel._stats_label`` 由此赋值。
    """
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(4, 0, 4, 0)
    lay.setSpacing(6)
    panel._vu = LevelBar()
    lay.addWidget(panel._vu)
    panel._stats_label = QLabel("min 0 | max 0 | avg 0.0 | 活动 0/512")
    panel._stats_label.setStyleSheet(
        f"color: {TEXT_SECONDARY}; font-size: 11px;")
    lay.addWidget(panel._stats_label, 1)
    row.setFixedHeight(24)
    return row


def build_io_section(panel) -> QWidget:
    """输入/输出对照条（R183 W2 #16）：面板级装配 + panel._io_view 赋值。"""
    panel._io_view = IoCompareView()
    return panel._io_view


def build_recorder_section(panel) -> QWidget:
    """录制/回放区（R183 W2 #14）：录制开关 + 时间轴滑条 + 反馈行。"""
    group = PanelSection("录制 / 回放")
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    panel._rec_btn = make_extras_button("录制", "开启/暂停环形录制（30s@20Hz）")
    panel._rec_btn.setCheckable(True)
    panel._rec_btn.setChecked(True)
    lay.addWidget(panel._rec_btn)
    panel._timeline = MonitorTimelineSlider(Qt.Orientation.Horizontal)
    panel._timeline.setRange(0, 0)
    panel._timeline.setEnabled(False)
    panel._timeline.setToolTip("回放滑条：拖到最右端恢复实时刷新")
    lay.addWidget(panel._timeline, 1)
    panel._rec_label = _caption("0.0s")
    panel._rec_label.setFixedWidth(40)
    lay.addWidget(panel._rec_label)
    group.add_widget(row)
    return group


def build_sparkline_section(panel) -> QWidget | None:
    """通道历史曲线区（R183 W2 #7）；无 pyqtgraph → None（调用方静默跳过）。"""
    panel._spark = ChannelSparkline()
    widget = panel._spark.widget()
    if not (panel._spark.available and widget is not None):
        return None
    group = PanelSection("选中通道历史 (10s)")
    group.add_widget(widget)
    return group


def build_export_row(panel) -> QWidget:
    """导出 + 动作反馈行（R183 W2 #8）：CSV 导出钮 + 最近动作反馈文本。"""
    group = PanelSection("导出")
    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(4)
    panel._export_btn = make_extras_button("导出当前域 CSV", "导出 512 行 ch,value(+delta) 为 CSV")
    col.addWidget(panel._export_btn)
    panel._action_label = QLabel("")
    panel._action_label.setWordWrap(True)
    panel._action_label.setStyleSheet(
        f"color: {TEXT_SECONDARY}; font-size: 11px; border-top: 1px solid {SEP_LINE};")
    col.addWidget(panel._action_label)
    group.add_layout(col)
    return group
