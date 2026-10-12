# -*- coding: utf-8 -*-
"""ChannelGrid 行分组折叠与行几何（R183 W2；深查② §四 #6）。

从 channel_grid.py 拆出（文件 ≤500 行门禁）：折叠行压缩为 ``ROW_BAND_H``
组头条，其余用完整 ``CELL_H``；本模块是**几何唯一出处**（绘制、命中测试、
脏区矩形都读这里），组表由 ``monitor_groups.row_groups_for_universe``
按灯具地址连续段生成。

以 Mixin 混入宿主；状态（``_row_groups`` / ``_collapsed``）仍在宿主上，
模块内不新增状态。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPen, QPainter  # noqa: F401 （类型标注用）

from ..qascade_theme import (
    BG_PANEL, SELECT_BLUE, cached_font, MONO_FONT_FAMILIES,
)
from .channel_grid_delta import DELTA_GOLD, draw_delta_band

ROW_BAND_H = 18                        # 折叠行保留的组头条高度（可点击展开）
GROUP_CHIP_W = 13                      # 展开态折叠小标宽（px）
GROUP_CHIP_H = 10                      # 展开态折叠小标高（px）
# 组头条底色：比 BG_PANEL_SUB 略亮的冷灰（与格子/热图可区分，主题内取色）
GROUP_BAND_BG = BG_PANEL


class ChannelGridGroupsMixin:
    """行几何 + 折叠组头绘制（混入宿主类）。"""

    # 常量别名（宿主类可读；绘制 Mixin 经 self._cell_h 访问，不改契约）
    _row_band_h = ROW_BAND_H

    # ── 行几何 ───────────────────────────────────

    def _row_h(self, row: int) -> int:
        return ROW_BAND_H if row in self._collapsed else self._cell_h

    def _row_top(self, row: int) -> int:
        return self._margin + self._ruler_h + sum(
            self._row_h(r) for r in range(row))

    def _total_h(self) -> int:
        return (self._margin * 2 + self._ruler_h
                + sum(self._row_h(r) for r in range(self._rows)))

    def _row_at(self, y: int) -> int:
        """像素 y → 行号（0-15），越界返回 -1。"""
        yy = y - self._margin - self._ruler_h
        if yy < 0:
            return -1
        for row in range(self._rows):
            hh = self._row_h(row)
            if yy < hh:
                return row
            yy -= hh
        return -1

    # ── 折叠控制（深查②#7）────────────────────────

    def set_row_groups(self, groups: dict) -> None:
        """注入 {行号: 灯具标签}；空表=退出分组视图。折叠状态裁剪失效项。"""
        self._row_groups = dict(groups or {})
        self._collapsed &= set(self._row_groups)
        self.updateGeometry()
        self.update()

    def row_group_label(self, row: int) -> str:
        return str(self._row_groups.get(row, ""))

    def is_collapsed(self, row: int) -> bool:
        return row in self._collapsed

    def toggle_row(self, row: int) -> None:
        """折叠/展开一行（组头点击）。"""
        if row not in self._row_groups:
            return
        if row in self._collapsed:
            self._collapsed.discard(row)
        else:
            self._collapsed.add(row)
        self.updateGeometry()
        self.update()

    def collapse_all(self, on: bool = True) -> int:
        """一键全折叠/全展开（右键菜单/快捷键用）；返回折叠行数。"""
        if on:
            self._collapsed = set(self._row_groups)
        else:
            self._collapsed.clear()
        self.updateGeometry()
        self.update()
        return len(self._collapsed)

    # ── 绘制（折叠组头条）────────────────────────

    def _draw_group_band(self, p, row: int, y: int, font_band) -> None:
        """折叠行组头条：灯具名 + 展开箭头（点击行即展开）。"""
        rect = QRect(self._margin + 1, y + 1,
                     self._cols * self._cell_w - 2, ROW_BAND_H - 2)
        p.fillRect(rect, QColor(GROUP_BAND_BG))
        p.setPen(QPen(QColor(SELECT_BLUE), 1))
        p.drawRect(rect)
        p.setFont(font_band if font_band is not None
                  else cached_font(11, families=MONO_FONT_FAMILIES))
        label = self.row_group_label(row)
        arrow = "+" if row in self._collapsed else "-"
        p.setPen(QColor(DELTA_GOLD))
        p.drawText(rect.adjusted(4, 0, -4, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{arrow} {label[:64]}")
        if self._compare and self._row_delta_hits(row):
            draw_delta_band(p, QRect(self._margin + 1, y + 1, 3, ROW_BAND_H - 2))

    def _row_delta_hits(self, row: int) -> bool:
        """该行是否有通道相对快照变化（组头条金色左边条判定）。"""
        mask = self.delta_mask()
        if mask is None:
            return False
        lo, hi = row * self._cols, row * self._cols + self._cols
        return bool(mask[lo:hi].any())

    # ── 展开态折叠小标（点击即折叠）─────────────────

    def _group_chip_rect(self, row: int) -> QRect:
        """展开态组起始行左上角的折叠小标矩形；非组行/已折叠返回空矩形。

        占位说明：小标压在首格左上角（该格通道号）——单行只一户，代价是
        每灯具组 1 个通道号被遮，换来"不点开就能折叠"的直达交互。
        """
        if row not in self._row_groups or row in self._collapsed:
            return QRect()
        return QRect(self._margin + 1, self._row_top(row) + 1,
                     GROUP_CHIP_W, GROUP_CHIP_H)

    def hit_group_chip(self, pos) -> int:
        """像素坐标 → 命中的组起始行号；未命中返回 -1（热图模式另算 x）。"""
        row = self._row_at(pos.y())
        if row < 0:
            return -1
        if self._display_mode == "heatmap":
            rect = QRect(2, self._row_top(row) + 2, GROUP_CHIP_W, GROUP_CHIP_H)
            return row if (row in self._row_groups and row not in self._collapsed
                           and rect.contains(pos)) else -1
        rect = self._group_chip_rect(row)
        return row if (not rect.isEmpty() and rect.contains(pos)) else -1

    def _consume_group_chip_click(self, e) -> bool:
        """左键点折叠小标 → 折叠该行；返回是否已消费（宿主 mousePress 守卫）。"""
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        row = self.hit_group_chip(e.pos())
        if row < 0:
            return False
        self.toggle_row(row)
        return True

    def _draw_group_chip(self, p, row: int, font_band=None) -> None:
        """展开态小标：13x10 圆角方块 + "-"（点击折叠该组）。"""
        rect = self._group_chip_rect(row)
        if rect.isEmpty():
            return
        p.fillRect(rect, QColor(GROUP_BAND_BG))
        p.setPen(QPen(QColor(SELECT_BLUE), 1))
        p.drawRect(rect)
        p.setFont(font_band if font_band is not None
                  else cached_font(9, families=MONO_FONT_FAMILIES))
        p.setPen(QColor(DELTA_GOLD))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "-")

    def _paint_heatmap_group_chips(self, p) -> None:
        """热图模式下的组小标（坐标独立于数值格：热图满窗密铺）。"""
        for row in self._row_groups:
            if row in self._collapsed:
                continue
            rect = QRect(2, self._row_top(row) + 2, GROUP_CHIP_W, GROUP_CHIP_H)
            p.fillRect(rect, QColor(GROUP_BAND_BG))
            p.setPen(QPen(QColor(SELECT_BLUE), 1))
            p.drawRect(rect)
            p.setFont(cached_font(9, families=MONO_FONT_FAMILIES))
            p.setPen(QColor(DELTA_GOLD))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "-")
