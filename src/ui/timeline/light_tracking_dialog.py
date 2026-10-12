# -*- coding: utf-8 -*-
"""灯跟踪参数采集对话框链（从 ``ClipView._ask_light_tracking`` 原样搬出）。

搬移动机（R178 架构门禁）：R17x 在 ``_ask_light_tracking`` 里塞进了一段"配置
说明确认框"，把该函数从 39 行涨到 73 行、CC 13→14，触发 `[复杂度恶化]`。
本模块把**整条参数采集链**逐行搬出来，`_ask_light_tracking` 只保留"取参数 →
emit"两步，语义与搬移前逐行一致：

  * 对话框顺序不变：说明确认框 → 颜色 → 来源 → 阈值 → 淡入 → 淡出 → 最大亮度。
  * 任一步取消 → 整体取消（原实现的 ``return``），不发信号。
  * 0 是合法值：取消一律用 ``None`` 表达，绝不用真值判断（EDGE-03）。
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.core.models import (
    DEFAULT_LIGHT_TRACKING_ATTACK, DEFAULT_LIGHT_TRACKING_RELEASE,
)

# `clip_light_tracking` 信号的尾部载荷（threshold, attack, release, color,
# source_key, max_brightness）—— 调用方拼上 clip.id 与 True 后原样 emit。
LightTrackingParams = Tuple[int, float, float, str, str, int]


def confirm_light_tracking_intro(view: QWidget) -> bool:
    """R17x 的"配置灯光跟踪参数"说明框；用户点 OK 返回 True。"""
    dlg = QDialog(view)
    dlg.setWindowTitle("自动灯跟踪设置")
    dlg.setMinimumWidth(420)

    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    layout.setContentsMargins(16, 12, 16, 12)

    header = QHBoxLayout()
    header.addWidget(QLabel("<b>配置灯光跟踪参数</b>"))
    header.addStretch(1)
    help_btn = QPushButton("?")
    help_btn.setMaximumWidth(30)
    help_btn.setToolTip("显示灯跟踪帮助（配套映射机制说明）")
    help_btn.clicked.connect(lambda: view._show_light_tracking_help())
    header.addWidget(help_btn)
    layout.addLayout(header)

    info_label = QLabel(
        "灯光跟踪让补光灯自动响应泵/阀门状态。\n系统使用 TopView 配套映射自动匹配设备。")
    info_label.setWordWrap(True)
    layout.addWidget(info_label)

    btn_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    btn_box.accepted.connect(dlg.accept)
    btn_box.rejected.connect(dlg.reject)
    layout.addWidget(btn_box)

    return dlg.exec() == QDialog.DialogCode.Accepted


def _ask_color(view: QWidget, clip) -> Optional[QColor]:
    """灯跟踪固定颜色；取消 / 非法返回 None。"""
    current_color = QColor(
        str(getattr(clip, "light_tracking_color", "#FFFFFF") or "#FFFFFF"))
    if not current_color.isValid():
        current_color = QColor("#FFFFFF")
    color = QColorDialog.getColor(current_color, view, "选择灯跟踪固定颜色")
    if not color.isValid():
        return None
    return color


def _ask_source(view: QWidget, clip) -> Optional[str]:
    """跟踪来源 -> ``"pump"`` / ``"valve"``；取消返回 None。"""
    current_source = str(
        getattr(clip, "light_tracking_source", "pump") or "pump").strip().lower()
    source, ok = QInputDialog.getItem(
        view,
        "自动灯跟踪",
        "跟踪来源：",
        ["跟踪泵数值", "跟踪阀门开合"],
        1 if current_source == "valve" else 0,
        False,
    )
    if not ok:
        return None
    return "valve" if "阀" in str(source) else "pump"


def _ask_threshold(view: QWidget, clip) -> Optional[int]:
    """触发阈值（0-255）；取消返回 None。"""
    th, ok = QInputDialog.getInt(
        view, "自动灯跟踪", "触发阈值（0-255）：",
        int(getattr(clip, "light_tracking_threshold", 128) or 128), 0, 255, 1)
    if not ok:
        return None
    return th


def _ask_fades(view: QWidget, clip) -> Optional[Tuple[float, float]]:
    """(灯亮淡入, 灯灭淡出) 秒；任一步取消返回 None。"""
    attack, ok = QInputDialog.getDouble(
        view, "自动灯跟踪", "灯亮淡入/响应速度（秒）：",
        float(getattr(clip, "light_tracking_attack", DEFAULT_LIGHT_TRACKING_ATTACK)
              or DEFAULT_LIGHT_TRACKING_ATTACK), 0.01, 10.0, 2)
    if not ok:
        return None
    release, ok = QInputDialog.getDouble(
        view, "自动灯跟踪", "灯灭淡出/释放速度（秒）：",
        float(getattr(clip, "light_tracking_release", DEFAULT_LIGHT_TRACKING_RELEASE)
              or DEFAULT_LIGHT_TRACKING_RELEASE), 0.01, 10.0, 2)
    if not ok:
        return None
    return attack, release


def _ask_max_brightness(view: QWidget, clip) -> Optional[int]:
    """最大亮度（0-255）；取消返回 None。"""
    _cur_mb = getattr(clip, "light_tracking_max_brightness", None)
    # 0 是合法值(用户输 0 意图灯保持灭), `or 255` 会把 0 显示成 255(EDGE-03)
    max_brightness, ok = QInputDialog.getInt(
        view, "自动灯跟踪", "最大亮度（0-255）：",
        int(255 if _cur_mb in (None, "") else _cur_mb), 0, 255, 1)
    if not ok:
        return None
    return max_brightness


def collect_light_tracking_params(
    view: QWidget, clip,
) -> Optional[LightTrackingParams]:
    """跑完整条对话框链；任一步取消返回 None（调用方不 emit）。"""
    if not confirm_light_tracking_intro(view):
        return None
    color = _ask_color(view, clip)
    if color is None:
        return None
    source_key = _ask_source(view, clip)
    if source_key is None:
        return None
    threshold = _ask_threshold(view, clip)
    if threshold is None:
        return None
    fades = _ask_fades(view, clip)
    if fades is None:
        return None
    max_brightness = _ask_max_brightness(view, clip)
    if max_brightness is None:
        return None
    return (
        int(threshold), float(fades[0]), float(fades[1]),
        color.name().upper(), source_key, int(max_brightness),
    )
