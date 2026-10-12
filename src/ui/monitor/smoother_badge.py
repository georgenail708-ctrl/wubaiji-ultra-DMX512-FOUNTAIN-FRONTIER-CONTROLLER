# -*- coding: utf-8 -*-
"""监视器「平滑」状态徽标（R183 A10 审计 P1 · S3b 可见性修复）。

背景（审计实锤）：全局 OutputSmoother 一旦在 Programmer 选择非旁通预设，将对
**全部输出**做 attack/release/死区过滤——`|diff|<0.5` 静默吸附、deadband 内小
变化永不跟随（如 deadband=3 时 ±3 内永不跟随）。监视器读的是平滑后的快照，
用户看到"值被压住/小变化丢失"会误判为"输出被破坏"。

本模块只做**可见性**（把当前平滑状态显式展示出来），不改输出语义——
平滑生效中的表现与硬件一致，无需也不可能由监视器单方面改变。

拆独立模块原因：monitor_panel.py 已接近 500 行门禁上限（A10 后 478 行），
新增逻辑放此小文件，monitor_panel 侧只留 2 行调用。
"""
from __future__ import annotations

from ..qascade_theme import STATUS_TEXT, TEXT_DISABLED

_SMOOTH_WARN = "#E6B455"     # BRIGHT_WARN：平滑生效中（值可能与直出不同）
_SMOOTH_OK = TEXT_DISABLED   # 旁通：与直出一致


def build_smoother_label(parent=None):
    """状态行内的「平滑」徽标（初始=未知，首次刷新即填实）。"""
    from PyQt6.QtWidgets import QLabel
    label = QLabel("平滑：—", parent)
    label.setStyleSheet(f"color: {_SMOOTH_OK}; font-size: 11px;")
    label.setToolTip("全局输出平滑状态（Programmer 页设置）。生效中时监视器/硬件"
                     "看到的是平滑后的值：小变化可能被死区吸收、大跳变被缓动。")
    return label


def _apply_active_badge(label, sm, memo) -> None:
    """平滑生效态徽标（文案+色+tooltip），三元组同文早退。"""
    try:
        _cp = sm.current_preset()
        name = str(getattr(_cp, "name", "")) if _cp is not None else "已启用"
    except Exception:
        name = "已启用"
    dead = 0
    try:
        dead = int(sm.get_param("deadband"))
    except Exception:
        dead = 0
    t = f"平滑：{name}"
    s = f"color: {_SMOOTH_WARN}; font-size: 11px;"
    tip = (f"全局输出平滑生效中（{name}）：监视器与硬件看到的是平滑后的值；"
           f"死区 {dead}（差值 ≤{dead} 的变化不跟随）。在 Programmer 页可切回旁通。")
    if (t, s, tip) != memo:
        label.setText(t)
        label.setStyleSheet(s)
        label.setToolTip(tip)
        label._smooth_memo = (t, s, tip)


def update_smoother_label(label) -> None:
    """按全局平滑器单例刷新徽标文案与颜色（任何异常静默——不许拖垮 20Hz 刷新）。

    R183 清查③#6 + 清查②②：(text, style, tip) 三元组同文早退——批B曾声称
    "同文守卫"但未落码，面板隐藏时仍 20Hz 无条件 setStyleSheet（每次重新
    解析 QSS + unpolish/polish）。
    """
    if label is None:
        return
    _memo = getattr(label, "_smooth_memo", None)
    try:
        from ...engine.output_smoother import get_smoother
        sm = get_smoother()
        if sm is None:
            if label._smooth_memo is not None:   # 清查⑤②#7：重置 memo 防
                label._smooth_memo = None        # 恢复生效后同文早退卡死
            label.setText("平滑：—")
            label.setStyleSheet(f"color: {_SMOOTH_OK}; font-size: 11px;")
            return
        if sm.is_bypass():
            t, s, tip = ("平滑：旁通",
                         f"color: {_SMOOTH_OK}; font-size: 11px;",
                         "全局输出平滑已旁通：监视器值与直出发送一致。")
            if (t, s, tip) != _memo:
                label.setText(t)
                label.setStyleSheet(s)
                label.setToolTip(tip)
                label._smooth_memo = (t, s, tip)
            return
        _apply_active_badge(label, sm, _memo)
    except Exception:
        try:   # 清查③②E 残留：异常回退同步回 OK 色（不留 WARN 色"——"）
            label.setText("平滑：—")
            label.setStyleSheet(f"color: {_SMOOTH_OK}; font-size: 11px;")
            label._smooth_memo = None
        except Exception:
            pass
