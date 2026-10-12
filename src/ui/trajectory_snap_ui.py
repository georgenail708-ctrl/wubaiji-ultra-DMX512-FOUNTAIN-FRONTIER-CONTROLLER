# -*- coding: utf-8 -*-
"""trajectory_snap_ui — 自动化录制的「节拍吸附 / 录制抽稀」行 UI（R180）。

自 programmer_automation 下沉（门禁债：该文件行数超 500）——吸附数学在
trajectory_grid（纯函数）、时钟在 trajectory_clock、抽稀在 trajectory_reduce；
本模块只负责把两个配置行接上曲线视图 / AutomationControls。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from . import qascade_theme as _th
from .widget_kit import NumSlider

if TYPE_CHECKING:  # 运行时不反向 import（programmer_automation 会 import 本模块）
    from .programmer_automation import AutomationControls


def build_snap_row(panel: QWidget) -> QWidget:
    """节拍吸附行：关/1拍/2拍/4拍；首次开启才挂音频驱动（懒 attach）。"""
    from .trajectory_clock import SNAP_DIVISIONS, TrajectoryClock

    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    clock = TrajectoryClock(panel)
    combo = QComboBox()
    combo.setFixedHeight(22)
    combo.setToolTip("画布画出节拍网格线（主格最亮，半拍/四分拍细分渐淡）；"
                     "拖点靠近格线时预对齐高亮，松手落在阈值内才吸附，"
                     "分段刀同样吸格。网格间距 = 拍长×N。开启时才挂音频驱动取 BPM")
    combo.addItem("关", 0)
    for d in SNAP_DIVISIONS:
        combo.addItem(f"{d} 拍", d)
    view = getattr(panel, "_traj_view", None)
    combo.currentIndexChanged.connect(
        lambda _i, c=combo, ck=clock, v=view: apply_snap_selection(c, ck, v))
    lay.addWidget(combo)
    lab = QLabel("（BPM 来自音频驱动）")
    lab.setStyleSheet("color:%s;font-size:10px;background:transparent;" % _th.TEXT_DISABLED)
    lay.addWidget(lab)
    lay.addStretch()
    return row


def apply_snap_selection(combo: QComboBox, clock, view) -> None:
    """把吸附选择应用到曲线视图；非零时懒挂 TrajectoryClock 到音频驱动。"""
    div = int(combo.currentData() or 0)
    if div and not clock.has_grid():
        clock.attach()
    if view is not None:
        view.set_snap_clock(clock)
        view.set_snap_division(div)


def build_reduce_row(auto: "AutomationControls") -> QWidget:
    """录制抽稀行：RDP 垂直容差（% 显示，0=关）；录制完成时抽样成控制点。"""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    eps = NumSlider(0.0, 5.0, auto.rdp_epsilon() * 100.0, decimals=1, suffix="%")
    eps.setToolTip("录制完成时 Ramer–Douglas–Peucker 抽稀的垂直容差（满量程 %，0=关）。\n"
                   "默认 0.5%≈1.3 个 DMX 级：抽稀后回放与原样本逐点误差 ≤ 容差。\n"
                   "长录一段不再产生海量控制点，且首尾点恒保留。")
    eps.valueChanged.connect(lambda _i: auto.set_rdp_epsilon(eps.float_value() / 100.0))
    lay.addWidget(eps)
    return row
