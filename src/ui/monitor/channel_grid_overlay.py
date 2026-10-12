# -*- coding: utf-8 -*-
"""ChannelGrid 叠加层：地址标注 + 冻结角标（R183 W2；深查② §四 #13/#5）。

从 channel_grid.py 拆出（文件 ≤500 行门禁）。以 Mixin 挂回宿主，方法名
在宿主命名空间可见；本模块不持状态，数据从宿主只读。

标注数据来自 ``monitor_annotations.annotation_spans``（灯具地址连续段），
纹理沿用既有格几何常量；热图模式坐标用 host 自己的 ``cw``（与
``_paint_heatmap`` 同坐标系，避免两套 x 映射打架）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QRectF
from PyQt6.QtGui import QColor, QPainter

from ..qascade_theme import (
    BG_INPUT, TEXT_PRIMARY, cached_font, MONO_FONT_FAMILIES, TEXT_SECONDARY,
)
from .monitor_annotations import fit_label

ANNOT_BAND_H = 12          # 标注带高（px，贴在格/热图行下沿）
ANNOT_OPACITY = 0.55       # 半透明：叠字不遮数值语义
ANNOT_BG = BG_INPUT
ANNOT_FG = TEXT_PRIMARY
FROZEN_BADGE_W = 46.0
FROZEN_BADGE_H = 16.0


class ChannelGridOverlayMixin:
    """标注叠加 + 冻结角标绘制（混入宿主类）。"""

    # ── 标注（#13）─────────────────────────────

    def set_annotations(self, spans, use_address: bool = False) -> None:
        """注入 {行: [(列, 列数, 标签)]}；空表=退出标注叠加。"""
        self._annotation_spans = dict(spans or {})
        self._annotation_address = bool(use_address)
        self.update()

    def annotations_enabled(self) -> bool:
        return bool(self._annotation_spans)

    def annotation_count(self) -> int:
        return sum(len(v) for v in self._annotation_spans.values())

    def _draw_annotation_band(self, p: QPainter, rect: QRect, label: str) -> None:
        """半透明标注带：底部深底条 + 居中浅色截断文本（无标签只画底条）。"""
        if rect.width() <= 6 or rect.height() <= 2:
            return
        p.setOpacity(ANNOT_OPACITY)
        p.fillRect(rect, QColor(ANNOT_BG))
        text = fit_label(label, rect.width() - 4)
        if text:
            p.setFont(cached_font(9, families=MONO_FONT_FAMILIES))
            p.setPen(QColor(ANNOT_FG))
            p.drawText(rect.adjusted(2, 0, -2, 0),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                       text)
        p.setOpacity(1.0)

    def _paint_row_annotations(self, p: QPainter, *, row: int, y: int,
                               cell_h: int) -> None:
        """数值模式：该行每个标注段贴下沿画带（跨行段续行只画底条）。"""
        spans = self._annotation_spans.get(row)
        if not spans:
            return
        top = y + cell_h - ANNOT_BAND_H
        for col, ncols, label in spans:
            rect = QRect(self._col_x(col), top,
                         int(self._cell_w) * int(ncols) - 2, ANNOT_BAND_H)
            self._draw_annotation_band(p, rect, label)

    def _paint_heatmap_annotations(self, p: QPainter) -> None:
        """热图模式：按热图自身列宽定位（与 `_paint_heatmap` 同坐标系）。

        cw 在此就地计算（= width/cols，热图满窗密铺，不含 MARGIN）——刻意
        不从 `_paint_heatmap` 借参数，避免触碰已提交模块的绘制契约。
        """
        if self.width() <= 0:
            return
        cw = self.width() / self._cols
        for row, spans in self._annotation_spans.items():
            if row in self._collapsed:
                continue
            top = self._row_top(row) + self._row_h(row) - ANNOT_BAND_H
            for col, ncols, label in spans:
                rect = QRectF(col * cw, top, ncols * cw - 2.0, ANNOT_BAND_H)
                self._draw_annotation_band(
                    p, QRect(int(rect.x()), int(rect.y()), int(rect.width()),
                             ANNOT_BAND_H), label)

    # ── 冻结角标（#5）──────────────────────────

    def _paint_frozen_badge(self, p: QPainter) -> None:
        """右上角"冻结/回放"角标：提示当前显示帧已定格（继续收数）。"""
        rect = QRectF(self.width() - FROZEN_BADGE_W - 8.0, 6.0,
                      FROZEN_BADGE_W, FROZEN_BADGE_H)
        p.setOpacity(0.85)
        p.fillRect(rect, QColor(ANNOT_BG))
        p.setPen(QColor("#E6B455"))
        p.setFont(cached_font(10, families=MONO_FONT_FAMILIES))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                   str(getattr(self, "_freeze_label", "冻结")))
        p.setOpacity(1.0)
