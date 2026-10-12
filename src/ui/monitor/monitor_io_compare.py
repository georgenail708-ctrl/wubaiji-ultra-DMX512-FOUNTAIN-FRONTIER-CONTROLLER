# -*- coding: utf-8 -*-
"""输入/输出对照视图（R183 W2；深查② §四 #16）。

需求原文：「输入/输出对照视图：同域 in vs out 双条（split 上下两 ChannelGrid/
轻量版；过滤双钮 `_input_btn`/`_output_btn` 现为隐藏态——核实后利用或说明
为何不启用）」。

现状核实（读码）：
  * ``_input_btn/_output_btn`` 是**过滤方向**开关（决定"应用过滤"写输入还是
    输出规则），不是视图开关；R55 用户点名顶栏精简后 hide() 保活。启用它们
    无法得到对照视图 → 本模块独立实现轻量双条（不复用它们，也不复活按钮）。
  * 轻量版而非两个 ChannelGrid：2×512 格全尺寸网格会再占 600px 高且重复
    选择/框选语义；本视图只需"同域 in vs out 逐通道对照"一眼可见 → 两条
    32 列色带（沿用热图温标 LUT，色彩语义与主网格一致）。
数据：out = ``nm.get_universe_data`` （既有显示来源）；in = ``nm.frame_raw_in``
（入网原始帧，仅接收路径发射）缓存。无 in 帧时上条按零显示并标注"无输入"。
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import QSizePolicy, QWidget
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen

from ..qascade_theme import (
    BG_DEEP, BG_ROOT, SEP_LINE, TEXT_DISABLED, TEXT_SECONDARY,
    cached_font, MONO_FONT_FAMILIES,
)
from .channel_grid_paint import HEAT_GAP, HEAT_ZERO, _heat_lut

IO_GUTTER_W = 26.0          # 左侧 IN/OUT 标签槽
IO_STRIP_H = 26.0           # 单条高
IO_VIEW_H = int(IO_STRIP_H * 2 + 4)


def _to_u8(raw) -> np.ndarray:
    """任意帧值 → uint8 定长 512（bytes 走 frombuffer；非法返回零帧）。"""
    if raw is None:
        return np.zeros(512, dtype=np.uint8)
    try:
        if isinstance(raw, (bytes, bytearray, memoryview)):
            arr = np.frombuffer(bytes(raw), dtype=np.uint8)
        else:
            arr = np.asarray(raw, dtype=np.uint8).reshape(-1)
    except (TypeError, ValueError):
        return np.zeros(512, dtype=np.uint8)
    out = np.zeros(512, dtype=np.uint8)
    n = min(arr.size, 512)
    out[:n] = arr[:n]
    return out


def io_compare_stats(in_data, out_data) -> dict:
    """纯函数：in/out 两帧 → {n_diff, max_diff, in_active, out_active, n}。"""
    a, b = _to_u8(in_data).astype(np.int16), _to_u8(out_data).astype(np.int16)
    d = np.abs(a - b)
    return {
        "n": int(a.size),
        "n_diff": int(np.count_nonzero(d > 0)),
        "max_diff": int(d.max()) if d.size else 0,
        "in_active": int(np.count_nonzero(a > 0)),
        "out_active": int(np.count_nonzero(b > 0)),
    }


class IoCompareView(QWidget):
    """轻量双条：上=输入帧，下=输出帧（同域、同 32 列网格、同一温标）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._in = np.zeros(512, dtype=np.uint8)
        self._out = np.zeros(512, dtype=np.uint8)
        self._has_in = False
        self.setFixedHeight(IO_VIEW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("上条=输入（网络收帧），下条=输出（混音后发送）")

    # ── 数据 ─────────────────────────────────────

    def set_frames(self, in_data, out_data, has_input: bool = True) -> None:
        """喂入两帧；分量未变不重绘。"""
        a, b = _to_u8(in_data), _to_u8(out_data)
        has = bool(has_input)
        if has == self._has_in and np.array_equal(a, self._in) \
                and np.array_equal(b, self._out):
            return
        self._in, self._out, self._has_in = a, b, has
        self.update()

    def stats(self) -> dict:
        return io_compare_stats(self._in, self._out)

    # ── 绘制 ─────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG_DEEP))
        p.setPen(QPen(QColor(SEP_LINE), 1))
        p.drawLine(0, 0, self.width(), 0)
        lut = _heat_lut()
        font = cached_font(9, families=MONO_FONT_FAMILIES)
        p.setFont(font)
        self._paint_strip(p, lut, row=0, data=self._in,
                          label="无输入" if not self._has_in else "IN")
        self._paint_strip(p, lut, row=1, data=self._out, label="OUT")
        p.end()

    def _paint_strip(self, p: QPainter, lut, *, row: int,
                     data: np.ndarray, label: str) -> None:
        """单条：512 格 1px 微缝色带 + 左槽标签。"""
        top = 2.0 + row * IO_STRIP_H
        p.setPen(QColor(TEXT_DISABLED if label != "OUT" else TEXT_SECONDARY))
        p.drawText(QRectF(2.0, top, IO_GUTTER_W - 4.0, IO_STRIP_H),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   label[:4])
        cw = max(0.5, (self.width() - IO_GUTTER_W) / 512.0)
        for i in range(data.size):
            v = int(data[i])
            rect = QRectF(IO_GUTTER_W + i * cw + HEAT_GAP / 2.0,
                          top + HEAT_GAP / 2.0,
                          max(0.5, cw - HEAT_GAP), IO_STRIP_H - HEAT_GAP)
            p.fillRect(rect, lut[v] if v > 0 else QColor(*HEAT_ZERO))
        p.setPen(QPen(QColor(BG_ROOT), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(IO_GUTTER_W, top, self.width() - IO_GUTTER_W - 0.5,
                          IO_STRIP_H))
