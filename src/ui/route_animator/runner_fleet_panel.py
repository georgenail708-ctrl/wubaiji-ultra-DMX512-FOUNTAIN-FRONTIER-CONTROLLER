# -*- coding: utf-8 -*-
"""跑动面板右侧「车队状态」栏构建（R183 W2 从 `runner_panel` 原样搬出）。

搬出动机：`runner_panel.py` 超架构基线（4084 行），本栏 ~100 行整体搬到本模块后
面板只留一行调用。**信号接线与文案逐行未改**（`_fleet_apply_*` / `_fleet_tree`
的槽仍在面板上，本模块只负责建控件与接线），派生行为零变化。

既有注入点（全部"面板源码零新逻辑"范式）：R1/R2/R6 运动控件、R10 链式编辑锁、
R5/R11/R3/R8/R14 轨道运维按钮 + R13 解挂回并。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QListWidget, QSlider,
    QTreeWidget, QVBoxLayout, QWidget,
)

from ..widget_kit import PanelSection
from ..flow_layout import FlowLayout
from ..qascade_theme import (
    BG_DEEP, BG_PANEL, BORDER_NORMAL, BRIGHT_ERROR, FOCUS_BLUE,
    SELECT_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_SELECTED_FG,
)

#: 与面板短名同源（局部别名，避免 import runner_panel 造成环）
_PANEL = BG_DEEP
_BORDER = BORDER_NORMAL
_FG = TEXT_PRIMARY
_FG_DIM = TEXT_SECONDARY
_RED = BRIGHT_ERROR

# 时间轴行解剖: 行高 24、选中 select_blue 白字 + 2px 左竖条、hover bg_panel(局部特化)。
DOPE_TREE_QSS = (
    f"QTreeWidget{{background:{BG_DEEP};color:{_FG};"
    f"border:1px solid rgba(255,255,255,17);border-radius:4px;"
    f"font-size:12px;outline:none;}}"
    f"QTreeWidget::item{{min-height:24px;max-height:24px;padding:0 4px;"
    f"border-left:2px solid transparent;border-radius:0;}}"
    f"QTreeWidget::item:hover{{background:{BG_PANEL};}}"
    f"QTreeWidget::item:selected{{background:{SELECT_BLUE};color:{TEXT_SELECTED_FG};"
    f"border-left:2px solid {FOCUS_BLUE};}}"
)
DOPE_LIST_QSS = (
    f"QListWidget{{background:{BG_DEEP};color:{_FG};"
    f"border:1px solid rgba(255,255,255,17);border-radius:4px;"
    f"font-size:12px;outline:none;}}"
    f"QListWidget::item{{min-height:24px;max-height:24px;padding:0;"
    f"border-left:2px solid transparent;border-radius:0;}}"
    f"QListWidget::item:hover{{background:{BG_PANEL};}}"
    f"QListWidget::item:selected{{background:{SELECT_BLUE};color:{TEXT_SELECTED_FG};"
    f"border-left:2px solid {FOCUS_BLUE};}}"
)

#: 批量运行模式下拉的标签表（与面板 `_TRAIN_MODE_LABELS` 同源同序）
TRAIN_MODE_LABELS = {
    "loop_forward": "循环正跑", "loop_reverse": "循环反跑",
    "bounce": "返程", "one_way_forward": "单程正跑",
    "one_way_reverse": "单程反跑",
}


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{_FG_DIM};font-size:11px;background:transparent;")
    return lbl


def _build_head(panel, lay) -> None:
    """栏头：标题 + 「已选 N」计数（`_fleet_update_sel_label` 写入）。"""
    head = QHBoxLayout()
    lbl = QLabel("车队状态")
    lbl.setStyleSheet(f"color:{_FG};font-size:13px;background:transparent;")
    head.addWidget(lbl)
    head.addStretch()
    panel._fleet_sel_lbl = _hint("已选 0")
    head.addWidget(panel._fleet_sel_lbl)
    lay.addLayout(head)


def _build_control_section(panel, lay) -> None:
    """运行控制区：批量模式/油门/解挂/清轨 + 三处注入（运动/链式/回并）。"""
    sec_ctl = PanelSection("运行控制", expanded=True)
    tools = QVBoxLayout()
    tools.setContentsMargins(0, 0, 0, 0)
    tools.setSpacing(6)
    panel._fleet_mode_combo = QComboBox()
    for key, label in TRAIN_MODE_LABELS.items():
        panel._fleet_mode_combo.addItem(label, key)
    panel._fleet_mode_combo.setToolTip("批量设置选中火车的运行模式(Ctrl/Shift 多选)")
    panel._fleet_mode_combo.currentIndexChanged.connect(
        lambda _i: panel._fleet_apply_mode(
            str(panel._fleet_mode_combo.currentData())))
    tools.addWidget(panel._fleet_mode_combo)
    tools.addLayout(_build_throttle_row(panel))
    tools.addLayout(_build_action_row(panel))
    from .runner_fleet_motion import inject_motion_controls
    inject_motion_controls(panel, tools)    # R1/R2/R6 斜坡/缓动/错峰
    from .runner_chain_edit import inject_chain_edit_toggle
    inject_chain_edit_toggle(panel, tools)  # R10 链式编辑锁
    sec_ctl.add_layout(tools)
    lay.addWidget(sec_ctl)


def _build_throttle_row(panel) -> QHBoxLayout:
    """批量油门滑条（标签右对齐 11px 次级色）。"""
    row = QHBoxLayout()
    row.setSpacing(6)
    lab = _hint("油门")
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lab.setMinimumWidth(40)
    row.addWidget(lab)
    panel._fleet_throttle = QSlider(Qt.Orientation.Horizontal)
    panel._fleet_throttle.setRange(0, 100)
    panel._fleet_throttle.setValue(100)
    panel._fleet_throttle.setToolTip("批量设置选中火车的油门")
    panel._fleet_throttle.valueChanged.connect(panel._fleet_apply_throttle)
    row.addWidget(panel._fleet_throttle, 1)
    return row


def _build_action_row(panel) -> FlowLayout:
    """解挂 / 回并 / 清除选中轨道（R13 回并闭环）。"""
    row = FlowLayout()
    row.setSpacing(6)
    buttons = (
        ("解挂选中车厢", "把选中车厢(含其后)解挂成独立子列车",
         panel._fleet_detach_selected),
        ("回并选中", "把选中的分队并回其主列车（尾部对齐，R13 解挂闭环）",
         panel._fleet_merge_selected),
        ("清除选中轨道", "清除选中火车被画的全部轨道(Ctrl/Shift 多选)",
         panel._clear_selected_train_tracks),
    )
    for text, tip, slot in buttons:
        btn = panel._card_btn(text, _RED)
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        row.addWidget(btn)
    return row


def _build_fleet_tree_section(panel, lay) -> None:
    """车辆编队树（行解剖对齐专业时间轴行）。"""
    sec_fleet = PanelSection("车辆编队", expanded=True)
    panel._fleet_tree = QTreeWidget()
    panel._fleet_tree.setHeaderLabels(["名称", "状态"])
    panel._fleet_tree.setColumnWidth(0, 150)
    panel._fleet_tree.setHeaderHidden(True)
    panel._fleet_tree.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection)
    panel._fleet_tree.setStyleSheet(DOPE_TREE_QSS)
    panel._fleet_tree.itemSelectionChanged.connect(panel._fleet_update_sel_label)
    panel._fleet_tree.itemDoubleClicked.connect(panel._on_fleet_item_double_clicked)
    sec_fleet.add_widget(panel._fleet_tree)
    lay.addWidget(sec_fleet, 1)


def _build_track_section(panel, lay) -> None:
    """轨道列表区：删轨/删车 + 轨道运维按钮注入（镜像/剖面/包络/段循环/顺序）。"""
    sec_tracks = PanelSection("轨道列表", expanded=True)
    track_btns = FlowLayout()
    track_btns.setContentsMargins(0, 0, 0, 2)
    track_btns.setSpacing(6)
    del_tracks_btn = panel._card_btn("删除选中轨道", _RED)
    del_tracks_btn.setToolTip("删除当前页选中的轨道(支持 Ctrl/Shift 多选)")
    del_tracks_btn.clicked.connect(panel._delete_selected_tracks)
    track_btns.addWidget(del_tracks_btn)
    del_trains_btn = panel._card_btn("删除选中火车", _RED)
    del_trains_btn.setToolTip("删除当前页选中的火车(支持 Ctrl/Shift 多选)")
    del_trains_btn.clicked.connect(panel._delete_selected_trains)
    track_btns.addWidget(del_trains_btn)
    sec_tracks.add_layout(track_btns)
    panel._track_list = QListWidget()
    panel._track_list.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection)
    panel._track_list.setStyleSheet(DOPE_LIST_QSS)
    panel._track_list.setMaximumHeight(120)
    sec_tracks.add_widget(panel._track_list)
    from .runner_track_ops import inject_track_ops_buttons
    inject_track_ops_buttons(panel, panel._track_list)   # R5/R11/R3/R8/R14
    lay.addWidget(sec_tracks)


def build_fleet_panel(panel) -> QWidget:
    """建右侧栏（顺序与修前逐行一致：头 → 运行控制 → 编队树 → 轨道列表）。"""
    w = QWidget()
    w.setMinimumWidth(240)
    w.setStyleSheet(f"background:{_PANEL};border-left:1px solid {_BORDER};")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(6)
    _build_head(panel, lay)
    _build_control_section(panel, lay)
    _build_fleet_tree_section(panel, lay)
    _build_track_section(panel, lay)
    return w


__all__ = ["DOPE_LIST_QSS", "DOPE_TREE_QSS", "TRAIN_MODE_LABELS",
           "build_fleet_panel"]
