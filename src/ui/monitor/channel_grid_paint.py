# -*- coding: utf-8 -*-
"""ChannelGrid 格子绘制与热图温标（R183 D 从 channel_grid.py 拆出）。

含三部分：连续温标色板/LUT；数值格绘制；512 格热图绘制。
以 Mixin 形式挂回 ChannelGrid（方法名不变，调用点零改动）；
本模块不持状态，所有数据从宿主 grid 只读。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient,
)

# S1 令牌化：画布配色重定向到中心主题令牌
# 注意：值→RGB 强度映射（_draw_cell 中 intensity 公式）为数据可视化语义，保持不动。
from ..qascade_theme import (
    BG_ROOT, BG_DEEP, BG_PANEL_SUB,
    SELECT_BLUE, FOCUS_BLUE, BORDER_NORMAL,
    TEXT_SECONDARY, TEXT_DISABLED, TEXT_SELECTED_FG, BRIGHT_INFO,
    cached_font, MONO_FONT_FAMILIES,
)

# ── R183 热图温标（数据映射色板：沿用既有 _paint_heatmap 显式色值先例）──
# stops 结构出处（2026-09-28 侦察）：
#   ParaView ColorMaps.json（分段线性 stops 的定义方式与"深底→亮端"走向）
#   https://raw.githubusercontent.com/Kitware/ParaView/master/Remoting/Views/ColorMaps.json
#   亮度单调性要求参考 matplotlib inferno/magma（Smith & van der Walt, SciPy 2015
#   "A Better Default Colormap for Matplotlib"；这两张表在 256 级整数采样下严格单调）。
# 说明：现有已发布色表中没有"深蓝→青→橙→黄"且亮度单调的一张表
#   （青与橙几乎等亮度，直插必然经过低彩度灰带；ParaView Cool to Warm 用近白中点，
#   非本用户路径）。因此按 6 档 stops 自建，并把青→橙过渡压缩到约 6% 量程内，
#   逐档校准使全量程 WCAG 相对亮度非递减（8192 点采样 0 负步），纯函数见 _heatmap_color。
HEAT_STOPS: tuple = (
    (0.000, (14, 22, 66)),     # 深蓝（暗）
    (0.180, (26, 66, 134)),    # 蓝
    (0.440, (15, 131, 176)),   # 青
    (0.500, (230, 145, 45)),   # 橙（窄过渡）
    (0.875, (247, 187, 63)),   # 琥珀
    (1.000, (253, 247, 150)),  # 淡黄
)
HEAT_ZERO = (20, 22, 28)   # v=0 中性暗底（比 BG_ROOT 更暗）凸显"无输出"
HEAT_HOT = 230             # 极值高亮环阈值：DMX ≥ 90% 视为近满输出
HEAT_GAP = 1.0             # 格间微缝宽度（px），保留密铺感
HEAT_LEGEND_OPACITY = 0.55  # 暖浅底叠加后仍保持次要图例层级


def _heatmap_color(v: float) -> QColor:
    """纯函数：0..1 连续值 → 热图色（分段线性，v=0 特判暗底）。

    单测契约：亮度随 v 单调递增（v>0）；相邻 8bit 采样无突跳。
    """
    if v <= 0.0:
        return QColor(*HEAT_ZERO)
    if v >= 1.0:
        return QColor(*HEAT_STOPS[-1][1])
    for (t0, c0), (t1, c1) in zip(HEAT_STOPS, HEAT_STOPS[1:]):
        if v <= t1:
            f = (v - t0) / (t1 - t0)
            return QColor(round(c0[0] + f * (c1[0] - c0[0])),
                          round(c0[1] + f * (c1[1] - c0[1])),
                          round(c0[2] + f * (c1[2] - c0[2])))
    return QColor(*HEAT_STOPS[-1][1])


_HEAT_LUT: list | None = None


def _heat_lut() -> list:
    """256 级色查表（首次构建后复用）：避免每帧 512 次插值。"""
    global _HEAT_LUT
    if _HEAT_LUT is None:
        _HEAT_LUT = [_heatmap_color(i / 255.0) for i in range(256)]
    return _HEAT_LUT


# ── R186-UP03②：单格绘制对象缓存（修前 512 格 × 每格 2-5 个 QColor/QPen
#    构造 ≈ 每秒 3 万次，数值模式实测 15.5ms/帧 @20Hz 刷新）。固定色/笔
#    模块级预建；值强度色按 intensity 桶缓存（int(val*0.6) ∈ 0..153）。
#    范式=draw_card_skin._COLORS/_PENS（共享只读；painter setPen/setFill
#    为值拷贝语义，先例在案）。分支与色值逐一对齐修前实现。
_C_SEL_ON = QColor(71, 114, 179, 220)      # SELECT_BLUE α220（选中·有值）
_C_SEL_OFF = QColor(40, 72, 125, 200)      # INFO_BLUE α200（选中·零值）
_C_SEL_BORDER = QColor(SELECT_BLUE)
_C_HOVER_BG = QColor(BG_PANEL_SUB)
_C_HOVER_BORDER = QColor(FOCUS_BLUE)
_C_ZERO_BG = QColor(BG_DEEP)
_C_IDLE_BORDER = QColor(255, 255, 255, 17)
_C_CH_NUM = QColor(TEXT_SECONDARY)
_C_CH_NUM_SEL = QColor(FOCUS_BLUE)
_C_VAL_ON = QColor(TEXT_SELECTED_FG)
_C_VAL_OFF = QColor(TEXT_DISABLED)
_C_VAL_OFF_SEL = QColor(BRIGHT_INFO)
_P_IDLE = QPen(_C_IDLE_BORDER, 1)
_P_SEL = QPen(_C_SEL_BORDER, 1)
_P_HOVER = QPen(_C_HOVER_BORDER, 1)
_INTENSITY_BG: dict = {}


def _intensity_bg(intensity: int) -> QColor:
    """值强度色 (i//3, i//2, i) 按 intensity 桶缓存。"""
    c = _INTENSITY_BG.get(intensity)
    if c is None:
        c = QColor(intensity // 3, intensity // 2, intensity)
        _INTENSITY_BG[intensity] = c
    return c


class ChannelGridPaintMixin:
    """ChannelGrid 的绘制方法集合（混入宿主类；无独立状态）。"""

    def _draw_cell(self, p: QPainter, *, x: int, y: int,
                   ch: int, val: int, selected: bool, hovered: bool,
                   font_small, font_val):
        """数值格：值→RGB 强度映射公式保持不变——数据可视化语义。"""
        if selected:
            bg = _C_SEL_ON if val > 0 else _C_SEL_OFF
            border_pen = _P_SEL
        elif hovered:
            bg = _C_HOVER_BG
            border_pen = _P_HOVER
        else:
            # 根据值深浅着色
            if val > 0:
                bg = _intensity_bg(int(val * 0.6))
            else:
                bg = _C_ZERO_BG
            border_pen = _P_IDLE

        rect = QRectF(x + 1, y + 1, self._cell_w - 2, self._cell_h - 2)
        p.fillRect(rect, bg)
        p.setPen(border_pen)
        p.drawRect(rect)

        # 通道号
        p.setFont(font_small)
        p.setPen(_C_CH_NUM_SEL if selected else _C_CH_NUM)
        p.drawText(x + 2, y + 2, self._cell_w - 4, 14,
                   Qt.AlignmentFlag.AlignLeft, str(ch))

        # 值
        p.setFont(font_val)
        if val > 0:
            p.setPen(_C_VAL_ON)
        else:
            p.setPen(_C_VAL_OFF_SEL if selected else _C_VAL_OFF)
        p.drawText(x + 1, y + 14, self._cell_w - 2, self._cell_h - 16,
                   Qt.AlignmentFlag.AlignCenter, str(val))

    def _paint_heatmap(self, p: QPainter) -> None:
        """512 通道热图：连续温标 + 1px 微缝 + 极值环 + 零值暗化 + 角标图例。

        坐标语义不变（col=idx%32, row=idx//32）；深查②#7 起行高走行几何
        （折叠行为 ROW_BAND_H 组头条，热图在该行只画细条）；深查②#1 起
        比快照模式为变化通道叠金色角标。
        """
        cw = self.width() / self._cols
        lut = _heat_lut()
        data = self._data
        # 极值环用深色墨：热图高端必为淡黄，白环在亮底上不可辨（已实测对比），
        # 深环则在全温区可读；BG_DEEP 复用主题令牌。
        pen_hot = QPen(QColor(BG_DEEP), 1)
        pen_sel = QPen(QColor(FOCUS_BLUE), 2)
        no_brush = Qt.BrushStyle.NoBrush
        mask = self.delta_mask() if self._compare else None

        for idx in range(self._cols * self._rows):
            col = idx % self._cols
            row = idx // self._cols
            # 每格四周各内缩 0.5px → 邻格之间露出 1px BG_ROOT 微缝
            rect = QRectF(col * cw + HEAT_GAP / 2.0,
                          self._row_top(row) + HEAT_GAP / 2.0,
                          cw - HEAT_GAP, self._row_h(row) - HEAT_GAP)
            v = int(data[idx])
            p.fillRect(rect, lut[v])
            if v >= HEAT_HOT and row not in self._collapsed:
                # 近满输出：1px 深色内环（内缩 1px，外留一圈本色作衬）
                p.setPen(pen_hot)
                p.setBrush(no_brush)
                p.drawRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
            if idx + 1 in self._selected:
                p.setPen(pen_sel)
                p.setBrush(no_brush)
                p.drawRect(rect)
            if mask is not None and mask[idx]:
                self._draw_delta_mark(p, right=(col + 1) * cw,
                                      top=self._row_top(row))

        if self._hover_ch:
            self._paint_heat_hover(p, cw)
        self._paint_heat_legend(p)

    def _paint_heat_hover(self, p: QPainter, cw: float) -> None:
        """悬停通道号提示（小字号，不喧宾）。"""
        ch_i = self._hover_ch - 1
        row = ch_i // self._cols
        p.setPen(QColor(FOCUS_BLUE))
        p.drawText(QRectF(ch_i % self._cols * cw, self._row_top(row),
                          cw, self._row_h(row)),
                   Qt.AlignmentFlag.AlignCenter, str(self._hover_ch))

    def _paint_heat_legend(self, p: QPainter) -> None:
        """右下角色标图例：小渐变条 + 0/255 标签，整体低透明度。"""
        bar_w, bar_h, pad = 84.0, 9.0, 8.0
        x = self.width() - bar_w - pad
        y = self.height() - bar_h - pad
        p.setOpacity(HEAT_LEGEND_OPACITY)
        # 底板：压住底下格子，保证 0/255 可读但不抢眼
        p.fillRect(QRectF(x - 26.0, y - 4.0, bar_w + 52.0, bar_h + 8.0),
                   QColor(BG_ROOT))
        p.setPen(QPen(QColor(BORDER_NORMAL), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(x - 26.0, y - 4.0, bar_w + 52.0, bar_h + 8.0))
        grad = QLinearGradient(x, 0.0, x + bar_w, 0.0)
        grad.setColorAt(0.0, QColor(*HEAT_ZERO))
        for t, c in HEAT_STOPS:
            grad.setColorAt(t, QColor(*c))
        p.fillRect(QRectF(x, y, bar_w, bar_h), QBrush(grad))
        p.setPen(QPen(QColor(BORDER_NORMAL), 1))
        p.drawRect(QRectF(x, y, bar_w, bar_h))
        p.setFont(cached_font(10, families=MONO_FONT_FAMILIES))
        p.setPen(QColor(TEXT_SECONDARY))
        p.drawText(QRectF(x - 26.0, y, 26.0, bar_h),
                   Qt.AlignmentFlag.AlignCenter, "0")
        p.drawText(QRectF(x + bar_w, y, 26.0, bar_h),
                   Qt.AlignmentFlag.AlignCenter, "255")
        p.setOpacity(1.0)
