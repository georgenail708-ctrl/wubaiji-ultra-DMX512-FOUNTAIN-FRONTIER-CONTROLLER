# -*- coding: utf-8 -*-
"""**场景推杆**（R178 革新）：per-scene 增益上限的悬浮小控件。

★ 需求：给单张场景卡一把 0-100 的"推杆"——拖着它实时压暗/推亮**这一个场景**
  的层输出（FadeRunner per-scene scale），同层其它场景不受影响（每层取各场景
  包络 ×scale 的 max，见 `scene_fade._envelope`）。

与 Programmer 手动推杆（EDITOR_LTP_LAYERS）的关系：那是**写值**路径（LTP 时间
新者赢，本就胜出，无需改）；本推杆是**层增益**路径（对正在跑的整场景做音台式
总控），两者正交、可叠加。

设计纪律：独立小窗（QTool），不往 DispatchPanel 上帝类加方法；引用挂在
`panel._fader_takeover_wins`（每场景一把，重开即复用聚焦）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (QLabel, QSlider, QVBoxLayout, QWidget)

#: 推杆窗体引用在面板上的挂点（{场景 id: QWidget}，防 GC + 重开去重）
_ATTR = "_fader_takeover_wins"


def _runner_of(panel):
    """面板的 FadeRunner（懒建，见 `scene_fade_ops._fade_runner`）。"""
    from .scene_fade_ops import _fade_runner
    return _fade_runner(panel)


def _on_value(panel, cid, value: int, label: QLabel) -> None:
    """推杆值 → per-scene 缩放（0..100 → 0..1）+ 刷新数值标签。"""
    try:
        _runner_of(panel).set_scene_gain_scale(str(cid), int(value) / 100.0)
        label.setText(f"{int(value)}%")
    except Exception:
        pass


def open_fader_takeover(panel, cid: str, scene_name: str = "") -> None:
    """弹出（或聚焦）某场景的推杆小窗，出现在鼠标附近。"""
    cid = str(cid or "")
    if not cid or panel is None:
        return
    wins = getattr(panel, _ATTR, None)
    if not isinstance(wins, dict):
        wins = {}
    old = wins.get(cid)
    if old is not None:
        try:
            old.raise_()
            old.activateWindow()
            return
        except RuntimeError:            # 已析构（面板重建等）→ 重开
            pass
    win = _build_window(panel, cid, scene_name)
    wins[cid] = win
    setattr(panel, _ATTR, wins)


def _build_window(panel, cid: str, scene_name: str) -> QWidget:
    """构建推杆小窗（竖向 0-100 滑杆 + 数值标签 + 复位按钮）。"""
    from PyQt6.QtWidgets import QPushButton

    win = QWidget(panel, Qt.WindowType.Tool)
    win.setWindowTitle(f"推杆接管 · {scene_name or cid[:8]}")
    win.setFixedWidth(88)
    win.setMinimumHeight(240)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(8, 10, 8, 8)

    value_lbl = QLabel("100%", win)
    value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(value_lbl)

    slider = QSlider(Qt.Orientation.Vertical, win)
    slider.setRange(0, 100)
    cur = 100
    try:
        cur = int(round(_runner_of(panel).scene_gain_scale(cid) * 100))
    except Exception:
        cur = 100
    slider.setValue(cur)
    slider.valueChanged.connect(
        lambda v, p=panel, c=cid, l=value_lbl: _on_value(p, c, v, l))
    lay.addWidget(slider, 1)

    reset_btn = QPushButton("复位", win)
    reset_btn.clicked.connect(lambda _=False: slider.setValue(100))
    lay.addWidget(reset_btn)

    win.move(QCursor.pos())
    win.show()
    return win


def reset_takeover_window(panel, cid: str) -> None:
    """场景释放/复位时把推杆窗滑杆归 100（UI 与 scale 同步；审计 M2）。

    滑杆 setValue(100) 触发 valueChanged → _on_value 写回 scale=1.0，
    与 FadeRunner.drop/reset 归还动作一致；窗保留（场景可再次点亮）。
    """
    wins = getattr(panel, _ATTR, None)
    if not isinstance(wins, dict):
        return
    win = wins.get(str(cid or ""))
    if win is None:
        return
    try:
        from PyQt6.QtWidgets import QSlider
        slider = win.findChild(QSlider)
        if slider is not None and slider.value() != 100:
            slider.setValue(100)
        win.update()
    except RuntimeError:
        pass   # 已析构


def close_takeover_window(panel, cid: str) -> None:
    """场景删除时关闭并摘除推杆窗（防死场景 id 残留窗）。"""
    wins = getattr(panel, _ATTR, None)
    if not isinstance(wins, dict):
        return
    win = wins.pop(str(cid or ""), None)
    if win is not None:
        try:
            win.close()
            win.deleteLater()
        except RuntimeError:
            pass


__all__ = ["open_fader_takeover", "reset_takeover_window", "close_takeover_window"]
