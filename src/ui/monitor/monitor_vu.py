# -*- coding: utf-8 -*-
"""选中通道迷你电平条（R183 W2；深查② §四 #3）。

需求原文：「选中通道 VU 横条：`_sel_label` 行改/加选中通道迷你电平条
（复用 `self._peak` 输出）」。

数据来源=监视器刷新链喂给 ChannelGrid 的**同一显示帧**（含 A10 G20 峰值
保持合成），不新增取数链。取选中集合的**最大值**（多选时"哪个通道在动"
一眼可见），未选择时取最近点击通道。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QSizePolicy, QWidget
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen

from ..qascade_theme import (
    BG_INPUT, SEP_LINE, TEXT_DISABLED, TEXT_SECONDARY,
    cached_font, MONO_FONT_FAMILIES,
)
from .channel_grid_paint import _heat_lut

VU_MIN_W = 108          # 紧凑下限：底栏 24px 行内不抢主内容
VU_MAX_W = 168          # 上限：多选/大值时也不吞掉统计文本空间
VU_HOLDS_PX = 3         # 峰值保持刻度（白点）宽


def meter_value(data, channels, fallback_ch: int = 0) -> int:
    """纯函数：显示帧 + 通道集合 → 电平取样值。

    选中集非空 → 取选中通道最大值（0 值也算，忠实反映"选中即静默"）；
    否则取 ``fallback_ch``（最近点击通道）；都没有返回 -1（未选择态）。
    """
    if data is None:
        return -1
    arr = data
    picked = [int(c) for c in (channels or ()) if 1 <= int(c) <= len(arr)]
    if not picked:
        if not (1 <= int(fallback_ch) <= len(arr)):
            return -1
        picked = [int(fallback_ch)]
    return int(max(int(arr[c - 1]) for c in picked))


class LevelBar(QWidget):
    """24px 迷你电平条：填充色走与热图同一温标，右端白点=峰值保持刻度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = -1
        self._peak = -1
        self.setFixedHeight(24)
        self.setMinimumWidth(VU_MIN_W)
        self.setMaximumWidth(VU_MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setToolTip("选中通道电平（含峰值保持）：取选中集最大值")

    def set_level(self, value: int, peak: int | None = None) -> None:
        """喂入电平（-1=未选择）；值未变不重绘。"""
        v = int(value)
        pk = v if peak is None else int(peak)
        if v == self._value and pk == self._peak:
            return
        self._value = v
        self._peak = pk if pk >= 0 else v
        self.update()

    def value(self) -> int:
        return self._value

    def paintEvent(self, event):
        p = QPainter(self)
        rect = QRectF(0.5, 4.5, self.width() - 1.0, self.height() - 9.0)
        p.fillRect(rect, QColor(BG_INPUT))
        p.setPen(QPen(QColor(SEP_LINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        if self._value > 0:
            frac = min(1.0, self._value / 255.0)
            fill = QRectF(rect.x() + 1.0, rect.y() + 1.0,
                          (rect.width() - 2.0) * frac, rect.height() - 2.0)
            p.fillRect(fill, _heat_lut()[min(255, self._value)])
        if self._peak > 0:
            # 峰值刻度紧贴填充右端（与填充同色系但更亮，不喧宾）
            x = rect.x() + 1.0 + (rect.width() - 2.0) * min(1.0, self._peak / 255.0)
            p.fillRect(QRectF(max(rect.x() + 1.0, x - VU_HOLDS_PX), rect.y() + 1.0,
                              VU_HOLDS_PX, rect.height() - 2.0),
                       QColor(255, 255, 255, 170))
        p.setFont(cached_font(10, families=MONO_FONT_FAMILIES))
        p.setPen(QColor(TEXT_DISABLED if self._value <= 0 else TEXT_SECONDARY))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                   "-" if self._value < 0 else str(self._value))
        p.end()
