# -*- coding: utf-8 -*-
"""ChannelGrid 快照对比 / delta 高亮（R183 D 从 channel_grid.py 拆出）。

深查②#1（Top10 #1）：存参考快照，|当前-快照|>DELTA_EPS 的通道染金色角标；
第三显示模式"Δ值"直接显示带符号差值。本模块只含纯逻辑 + 绘制助手，
不自持状态（_snapshot/_compare/_data 仍在 ChannelGrid 上）。
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont

from ..qascade_theme import (
    BG_DEEP, BG_PANEL_SUB, SELECT_BLUE, FOCUS_BLUE,
    TEXT_SECONDARY, TEXT_DISABLED,
)

# delta=|当前-快照|>DELTA_EPS 的通道：金色角标（三角）+ 第三显示模式"Δ值"。
# 金色取语义 WARN 金（同 smoother_badge/_G 系先例，区别于选择蓝/极值环）。
DELTA_EPS = 2
DELTA_GOLD = QColor(230, 180, 85)      # BRIGHT_WARN
DELTA_GOLD_DIM = QColor(230, 180, 85, 60)
DELTA_MARK = 9                         # 角标直角边长（px）


def normalize_snapshot(snap) -> np.ndarray | None:
    """快照归一化：None=清除；否则取前 512 字节补齐为 uint8 定长 512。"""
    if snap is None:
        return None
    arr = np.asarray(snap, dtype=np.uint8).reshape(-1)[:512]
    base = np.zeros(512, dtype=np.uint8)
    base[: arr.size] = arr
    return base


def delta_mask_of(data: np.ndarray, snapshot: np.ndarray) -> np.ndarray:
    """纯函数：|当前-快照|>DELTA_EPS 的布尔掩码（int16 运算防 uint8 回绕）。"""
    d = np.asarray(data, dtype=np.int16) - np.asarray(snapshot, dtype=np.int16)
    return np.abs(d) > DELTA_EPS


def delta_cell_style(val: int, snap: int, selected: bool, hovered: bool):
    """纯函数：Δ值格 → (背景色, 描边色)；变化越大底色越暖、越亮。"""
    ad = abs(val - snap)
    if selected:
        return QColor(40, 72, 125, 200), QColor(SELECT_BLUE)
    if hovered:
        return QColor(BG_PANEL_SUB), QColor(FOCUS_BLUE)
    if ad > DELTA_EPS:
        t = min(1.0, ad / 128.0)
        return (QColor(round(60 + t * 90), round(50 + t * 60), 24),
                QColor(DELTA_GOLD))
    return QColor(BG_DEEP), QColor(255, 255, 255, 17)


def draw_delta_cell(p: QPainter, *, rect: QRect, ch: int, val: int, snap: int,
                    selected: bool, hovered: bool, font: QFont) -> None:
    """Δ值模式格：背景按 |delta| 强度，文本为带符号差值（纯绘制，无状态）。

    rect 由调用方按格几何给出（x+1, y+1, CELL_W-2, CELL_H-2），本模块不
    依赖 channel_grid 的几何常量，避免循环 import。
    """
    d = val - snap
    bg, border = delta_cell_style(val, snap, selected, hovered)
    p.fillRect(rect, bg)
    p.setPen(QPen(border, 1))
    p.drawRect(rect)
    # 通道号
    p.setFont(font)
    p.setPen(QColor(FOCUS_BLUE) if selected else QColor(TEXT_SECONDARY))
    p.drawText(rect.x() + 1, rect.y() + 1, rect.width() - 2, 14,
               Qt.AlignmentFlag.AlignLeft, str(ch))
    # 带符号差值
    p.setPen(QColor(DELTA_GOLD) if abs(d) > DELTA_EPS else QColor(TEXT_DISABLED))
    p.drawText(rect.x(), rect.y() + 13, rect.width(), rect.height() - 14,
               Qt.AlignmentFlag.AlignCenter, f"{d:+d}")


def draw_delta_mark(p: QPainter, right: float, top: float) -> None:
    """金色角标（右上三角）：|当前-快照|>DELTA_EPS 的通道。

    坐标为格右上角（数值模式 x+CELL_W；热图 (col+1)*cw，含 MARGIN 差异）。
    """
    tri = [QPoint(round(right) - 1, round(top) + 1),
           QPoint(round(right) - 1 - DELTA_MARK, round(top) + 1),
           QPoint(round(right) - 1, round(top) + 1 + DELTA_MARK)]
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(DELTA_GOLD))
    p.drawPolygon(tri)


def draw_delta_band(p: QPainter, rect: QRect) -> None:
    """折叠行组头条上的 delta 提示：金色左边条（命中区间由调用方判定）。"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(DELTA_GOLD))
    p.drawRect(rect)
