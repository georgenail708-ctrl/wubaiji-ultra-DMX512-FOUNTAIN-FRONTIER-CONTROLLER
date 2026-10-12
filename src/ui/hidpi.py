# -*- coding: utf-8 -*-
"""HiDPI 离屏位图（01 清单 C7「所有带全局预览的框的渲染精度都不行」根因修复）。

根因：各画布/小地图/预览的离屏缓存用 ``QPixmap(逻辑宽, 逻辑高)`` 建，未设
devicePixelRatio——用户实测机为 150% 缩放（DPR 1.5），缓存按 1× 栅格化后被放大 1.5
倍贴屏 → 点阵、线条、文字全部发糊。Qt 的做法（Qt HiDPI 文档 "Drawing high
resolution versions of pixmaps"）：按物理像素建图 + ``setDevicePixelRatio``，
之后 QPainter 仍用逻辑坐标绘制、drawPixmap 按逻辑尺寸贴屏，调用方其余代码不变。

注意：设了 DPR 的位图 ``size()/width()`` 返回**物理**像素；做缓存命中判断时
用 :func:`logical_size` 比较，否则每帧都会判成"尺寸变了"而重建。
"""
from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QPixmap

__all__ = ["screen_dpr", "hidpi_pixmap", "logical_size"]


def screen_dpr(widget=None) -> float:
    """控件所在屏（无控件 → 主屏）的 devicePixelRatio，最低 1.0。"""
    try:
        if widget is not None:
            return max(1.0, float(widget.devicePixelRatioF()))
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        return max(1.0, float(scr.devicePixelRatio())) if scr is not None else 1.0
    except Exception:
        return 1.0


def hidpi_pixmap(w: int, h: int, widget=None) -> QPixmap:
    """逻辑尺寸 w×h 的离屏位图（物理像素 = 逻辑 × DPR；内容未初始化，调用方 fill）。"""
    dpr = screen_dpr(widget)
    pm = QPixmap(max(1, int(round(int(w) * dpr))), max(1, int(round(int(h) * dpr))))
    pm.setDevicePixelRatio(dpr)
    return pm


def logical_size(pm) -> QSize:
    """位图的逻辑尺寸（缓存命中比较用；兼容未设 DPR 的旧位图）。"""
    try:
        return pm.deviceIndependentSize().toSize()
    except Exception:
        return pm.size()
