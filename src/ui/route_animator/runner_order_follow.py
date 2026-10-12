# -*- coding: utf-8 -*-
"""R14 补遗：跑动顺序表**跟随**（可选开关，默认关 = 零回归）。

深查③ R14 只做了「按顺序生成轨道」按钮（一次性）。本模块把它变成可选联动：
开启后按固定节拍轮询当前页顺序表的指纹，顺序真变化时自动重建顺序轨道
（顺序即轨道，消除二次描线）；关闭时一行代码都不跑（默认零回归，也避免
覆盖用户手工描的线）。

拆出动机：`runner_track_ops.py` 逼近 500 行门禁。
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox

#: 跟随轮询间隔（ms）——顺序表是人手编辑，1.5s 足够且几乎零开销
ORDER_POLL_MS = 1500


def order_follow_enabled(panel) -> bool:
    """跟随开关是否开启（控件未建 → False）。"""
    return bool(getattr(panel, "_order_follow", False))


def order_fingerprint(panel) -> tuple:
    """当前页顺序表指纹（顺序变更检测；无工程/无表 → 空元组）。"""
    from .runner_order_track import _project_of, active_sheet, sheet_order_ids
    proj = _project_of(panel)
    if proj is None:
        return ()
    return tuple(sheet_order_ids(active_sheet(proj)))


def order_follow_tick(panel) -> bool:
    """定时器槽：指纹变化且非空 → 重建顺序轨道。返回是否触发重建。"""
    if not order_follow_enabled(panel):
        return False
    fp = order_fingerprint(panel)
    if fp == getattr(panel, "_order_follow_fp", None):
        return False
    panel._order_follow_fp = fp
    if not fp:
        return False
    from .runner_order_track import apply_order_as_track
    apply_order_as_track(panel)
    return True


def set_order_follow(panel, on: bool) -> None:
    """开关顺序表跟随；开启时立即记一次指纹（避免开关本身触发重建）。"""
    panel._order_follow = bool(on)
    panel._order_follow_fp = order_fingerprint(panel)
    try:
        panel._set_status("顺序表跟随已开启（顺序变更自动重建顺序轨道）"
                          if on else "顺序表跟随已关闭")
    except Exception:
        pass


def inject_order_follow(panel, row) -> None:
    """把「顺序跟随」开关插到轨道操作按钮行（含自有轮询定时器）。"""
    if row is None or getattr(panel, "_order_follow_injected", False):
        return
    panel._order_follow_injected = True
    box = QCheckBox("顺序跟随")
    box.setToolTip("开启后：跑动顺序表（当前页）变更时自动按新顺序重建轨道"
                   "（默认关，避免覆盖手工描线）")
    box.toggled.connect(lambda on, p=panel: set_order_follow(p, bool(on)))
    row.addWidget(box)
    panel._order_follow = False
    panel._order_follow_fp = None
    timer = getattr(panel, "_order_follow_timer", None)
    if timer is None:
        timer = QTimer(panel)
        timer.setInterval(ORDER_POLL_MS)
        timer.timeout.connect(lambda p=panel: order_follow_tick(p))
        panel._order_follow_timer = timer
    timer.start()


__all__ = ["ORDER_POLL_MS", "inject_order_follow", "order_fingerprint",
           "order_follow_enabled", "order_follow_tick", "set_order_follow"]
