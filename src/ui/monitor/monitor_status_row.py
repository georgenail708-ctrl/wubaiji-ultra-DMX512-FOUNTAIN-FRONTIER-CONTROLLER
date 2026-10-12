# -*- coding: utf-8 -*-
"""MonitorPanel 状态行装配（R183 D 从 monitor_panel.py 拆出，压文件行数）。

R178：status_label + 热图切换按钮 + 横条；A10-S3b：平滑徽标同排。
拆分后调用点不变（``_build_status_row(panel)`` 返回四元组），monitor_panel
以同名 re-export 保持 ``mp._build_status_row`` 等既有引用可用。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton


def _sync_smooth_badge(panel):
    """A10-S3b 单行调用壳：平滑徽标刷新（异常全吞，不拖垮 20Hz 链）。"""
    try:
        from .smoother_badge import update_smoother_label
        update_smoother_label(getattr(panel, "_smooth_label", None))
    except Exception:
        pass


def _build_status_row(panel):
    """网络状态行装配（R178：status_label + 热图切换按钮 + 横条）。

    从 _setup_ui 拆出（压基线）；返回 (status_label, mode_btn, smooth_label,
    wrap_layout)，main_layout.insertLayout(0, wrap) 由调用方完成。
    A10-S3b：smooth_label = 全局平滑状态徽标（smoother_badge 小模块装配）。
    R183 W2：同排补 冻结 / 标注 / 分组 三钮（顶栏直达，深查② §四 #5/#13/#6）。
    """
    from .smoother_badge import build_smoother_label
    status_label = QLabel("●  未连接")
    mode_btn = _build_heatmap_toggle(panel, panel._on_display_mode_toggled)
    smooth_label = build_smoother_label(panel)
    wrap = QHBoxLayout()
    wrap.setContentsMargins(12, 0, 8, 0)
    wrap.addWidget(status_label)
    wrap.addSpacing(10)
    wrap.addWidget(smooth_label)
    wrap.addStretch()
    _add_w2_toggles(panel, wrap)
    wrap.addWidget(mode_btn)
    return status_label, mode_btn, smooth_label, wrap


def _add_w2_toggles(panel, wrap) -> None:
    """W2 顶栏三钮：冻结（checkable）/ 标注（三档循环）/ 分组（checkable）。

    装配为 ``panel._freeze_btn`` / ``panel._annot_btn`` / ``panel._group_btn``；
    样式与热图钮同源（``make_extras_button``，R55 顶栏精简不新增分组行）。
    """
    from .monitor_panel_view import make_extras_button
    panel._freeze_btn = make_extras_button("冻结", "定格当前画面（继续收数，可截图比对）")
    panel._freeze_btn.setCheckable(True)
    panel._freeze_btn.setFixedWidth(56)
    panel._annot_btn = make_extras_button("标注", "叠加灯具名/地址标注（三档循环）")
    panel._annot_btn.setFixedWidth(56)
    panel._group_btn = make_extras_button("分组", "按灯具地址段折叠通道行（点击组头展开）")
    panel._group_btn.setCheckable(True)
    panel._group_btn.setFixedWidth(56)
    for btn in (panel._freeze_btn, panel._annot_btn, panel._group_btn):
        wrap.addWidget(btn)


def _build_heatmap_toggle(panel, toggle_slot=None):
    """热图/数值切换按钮（R178；从 _setup_ui 拆出压基线）。"""
    btn = QPushButton("热图")
    btn.setCheckable(True)
    btn.setFixedHeight(22)
    btn.setFixedWidth(64)
    btn.setToolTip("切换 数值模式 / 512 通道热图模式")
    btn.setStyleSheet(
        "QPushButton{background:#303030;color:#e6e6e6;border:1px solid #555;"
        "border-radius:3px;font-size:11px;}"
        "QPushButton:checked{background:#4772b3;color:#ffffff;border-color:#71a8ff;}")
    if toggle_slot is not None:
        btn.toggled.connect(toggle_slot)
    return btn
