# -*- coding: utf-8 -*-
"""调度台表格的**绘制方法**（从 `table_view` 拆出：单文件 ≤500 行）。

为什么独立成 mixin 模块：绘制（纯呈现）与交互/布局（状态与命中）是两件事；
拆开后 `table_view` 只剩数据/布局/输入，绘制细节单独可读、可审。

本 mixin 只读宿主视图的公开/半公开状态（`_model`/`_values`/`_marks`/`_sources`/
`_sel_rows`/`_sel_cols`/`_hover`/`_selected`）与 `_section_geometry()`；不写状态。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFontMetrics, QLinearGradient, QPainter,
                         QPainterPath, QPen)

from .. import qascade_theme as _th
from ...processing.dispatch.table_model import (
    SUMMARY_PANEL,
    TableSection,
    cell_display,
)
from .table_style import (
    CELL_R,
    _LayItem,
    GRID_A,
    GRP_H,
    GRP_HDR_H,
    GRP_INDENT,
    HDR_H,
    PAD,
    ROW_H,
    cell_font_px,
    SEC_H,
    VALUE_BAR_H,
    ZEBRA_A,
    _CHANNEL_SECTIONS,
    _SOURCE_COLOR,
    _SOURCE_GLYPH,
    _STATE_EMPTY,
    _STATE_NA,
    _split_col_key,
    mono_font,
    ui_font,
)


#: 颜色缓存：绘制期大量 `QColor(...) + setAlpha` 重复创建是每格耗时的主因之一。
#  ★ 键含 alpha：同一 (r,g,b,a) 复用同一 QColor 实例（只读使用，安全）。
_COLOR_CACHE: Dict[Tuple[int, int, int, int], QColor] = {}


def _qc(r: int, g: int, b: int, a: int = 255) -> QColor:
    """取（带缓存的）QColor。绘制热路径专用。"""
    key = (r, g, b, a)
    c = _COLOR_CACHE.get(key)
    if c is None:
        c = QColor(r, g, b, a)
        _COLOR_CACHE[key] = c
    return c


def _qhex(hex_color: str, a: int = 255):
    """由 hex 取带缓存的 QColor（主题色表用）。"""
    h = str(hex_color or "").lstrip("#")
    try:
        r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    except Exception:
        r = g = b = 138
    return _qc(r, g, b, a)


#: 常用 QPen 常量（绘制热路径复用；QPen 每次 new 也要分配）
_pen_na = QPen(QColor(_th.BORDER_NORMAL), 1)
_pen_grid = QPen(QColor(_th.BORDER_NORMAL), 1)

#: 圆角底路径缓存：按 (宽, 高, 圆角×10) 复用同一个 QPainterPath（相对坐标），
#  绘制时用 `translated()` 平移到目标位置 → 省掉每格重建 path 的开销。
_PATH_CACHE: Dict[Tuple[int, int, int], QPainterPath] = {}


def _rounded_path(w: int, h: int, radius: float) -> QPainterPath:
    key = (int(w), int(h), int(round(radius * 10)))
    path = _PATH_CACHE.get(key)
    if path is None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, max(1, w - 1), max(1, h - 1)),
                            radius, radius)
        _PATH_CACHE[key] = path
    return path


class TablePaintMixin:
    """绘制方法集合（由 `FixtureTableView` 继承）。"""

    def _draw_section(self, p: QPainter, it: _LayItem, f_sec, f_grp) -> None:
        """分区条 = 模块来源（用户专属特色）。满玻璃 + 分类感左侧色条。"""
        r = it.rect
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QColor(255, 255, 255, 16))
        grad.setColorAt(1.0, QColor(255, 255, 255, 3))
        p.fillRect(r, QBrush(grad))
        p.setPen(QPen(QColor(_th.FOCUS_BLUE), 2))
        p.drawLine(r.left(), r.top() + 3, r.left(), r.bottom() - 3)
        p.setFont(f_sec); p.setPen(_qhex(_th.TEXT_PRIMARY))
        p.drawText(r.adjusted(8, 0, -60, 0),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   str(it.label))
        p.setFont(f_grp); p.setPen(QColor(_th.TEXT_DISABLED))
        p.drawText(r.adjusted(0, 0, -8, 0),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                   f"{int(it.count)} 行")

    def _draw_group(self, p: QPainter, it: _LayItem, f_grp) -> None:
        """组条：母（已创建组 ■）= 实底；子（临时选中 ▌）= 虚线 + 缩进 + 角标。"""
        r = it.rect
        is_temp = int(it.depth) >= 2
        if is_temp:
            p.fillRect(r, _qc(255, 255, 255, 8))
            p.setPen(QPen(QColor(_th.BRIGHT_WARN), 2))
            p.drawLine(r.left(), r.top() + 2, r.left(), r.bottom() - 2)
        else:
            p.fillRect(r, QColor(255, 255, 255, 16))
            p.setPen(QPen(_qhex(_th.BORDER_NORMAL), 1))
            p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)
        p.setFont(f_grp)
        if is_temp:
            p.setPen(QColor(_th.TEXT_SECONDARY))
            p.drawText(r.adjusted(6, 0, -70, 0),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       f"▌ {it.folder}")
            p.setPen(QColor(_th.STATUS_WARNING_INK))
            p.drawText(r.adjusted(0, 0, -6, 0),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       "临时选中")
        else:
            p.setPen(_qhex(_th.TEXT_PRIMARY))
            p.drawText(r.adjusted(6, 0, -6, 0),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       f"■ {it.folder}")

    def _draw_group_header(self, p: QPainter, it: _LayItem, f_grp) -> None:
        """参数分组条：满玻璃 + 各分组分类色下边线（列光谱的"发源"）。"""
        r = it.rect
        p.fillRect(r, _qc(255, 255, 255, 10))
        sec = getattr(it, "sec", None) or self._section(it.section)
        if sec is None:
            return
        xs, widths = self._section_geometry(sec)
        y_base = r.bottom() - 1
        for c in sec.columns:
            x = xs.get(c.key, r.left())
            w = widths.get(c.key, 44)
            col = self._col_color(c)
            seg = QRect(x, r.top(), w, r.height())
            grad = QLinearGradient(0, seg.top(), 0, seg.bottom())
            top = QColor(col); top.setAlpha(26)
            bot = QColor(col); bot.setAlpha(8)
            grad.setColorAt(0.0, top); grad.setColorAt(1.0, bot)
            p.fillRect(seg, QBrush(grad))
            p.setPen(QPen(col, 2))
            p.drawLine(x, y_base - 1, x + w, y_base - 1)
        # 分组名（去重后按首次出现位置画）
        p.setFont(f_grp)
        drawn = set()
        for c in sec.columns:
            if c.group in drawn:
                continue
            drawn.add(c.group)
            x = xs.get(c.key, r.left())
            p.setPen(QColor(_th.TEXT_DISABLED))
            p.drawText(QRect(x + 5, r.top(), 120, r.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       str(c.group))

    def _draw_col_header(self, p: QPainter, it: _LayItem, f_hdr) -> None:
        """值类型列头（满玻璃）。列内有 S 标记时右侧点一个小三角。"""
        r = it.rect
        cols = it.col or []
        sec = getattr(it, "sec", None) or self._section(it.section)
        if sec is None:
            return
        xs, widths = self._section_geometry(sec)
        p.fillRect(r, QColor(255, 255, 255, 6))
        p.setPen(QPen(_qhex(_th.BORDER_NORMAL), 1))
        p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)
        p.setFont(f_hdr)
        fm = QFontMetrics(f_hdr)
        for c in cols:
            x = xs.get(c.key, r.left())
            w = widths.get(c.key, 44)
            seg = QRect(x, r.top(), w, r.height())
            if self._col_sel(c.key):
                p.fillRect(seg, QColor(_th.BRAND_ORANGE_SELECTED))
            p.setPen(QPen(_qhex(_th.BORDER_NORMAL), 1))
            p.drawLine(seg.right() - 1, seg.top() + 3, seg.right() - 1, seg.bottom() - 3)
            has_mark = self._col_has_mark(c.key)
            tw = w - 14 - (10 if has_mark else 0)
            p.setPen(QColor(_th.TEXT_TERTIARY))
            p.drawText(QRect(x + 5, r.top(), max(10, tw), r.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       fm.elidedText(str(c.label), Qt.TextElideMode.ElideRight, max(10, tw)))
            if has_mark:
                # S 列角标：一眼看出哪列有"要存"的格
                tri = QPainterPath()
                bx = x + w - 9
                by = r.top() + r.height() // 2
                tri.moveTo(bx, by - 3.5)
                tri.lineTo(bx + 5, by)
                tri.lineTo(bx, by + 3.5)
                tri.closeSubpath()
                p.fillPath(tri, QBrush(_qhex(_th.KEYFRAME_GOLD)))

    def _draw_row_header(self, p: QPainter, it: _LayItem, f_row) -> None:
        """行头 = 设备名（子设备带序号）。hover/选中整行高亮。"""
        r = it.rect
        row = it.row
        rid = row.row_id
        if rid in self._sel_rows:
            p.fillRect(r, QColor(_th.BRAND_ORANGE_SELECTED))
        elif row.is_agg_row():
            # 整合行（按组）：左侧分类色细条 + 略亮底 → 一眼区分"这一行代表 N 台"
            p.fillRect(r, _qc(255, 255, 255, 12))
            p.setPen(QPen(_qhex(_th.BRIGHT_INFO), 2))
            p.drawLine(r.left(), r.top() + 3, r.left(), r.bottom() - 3)
        elif not row.is_sub_row():
            p.fillRect(r, _qc(255, 255, 255, 8))
        p.setPen(QPen(_qhex(_th.BORDER_NORMAL), 1))
        p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)
        p.setFont(f_row)
        if row.is_agg_row():
            p.setPen(_qhex(_th.TEXT_PRIMARY))
        else:
            p.setPen(_qhex(_th.TEXT_DISABLED if row.is_sub_row()
                            else _th.TEXT_SECONDARY))
        fm = QFontMetrics(f_row)
        p.drawText(r.adjusted(7, 0, -6, 0),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(str(row.label), Qt.TextElideMode.ElideRight,
                                 max(10, r.width() - 13)))

    # ── 绘制：数据格（浅玻璃）────────────────────────────────────────────
    def _draw_cell(self, p: QPainter, it: _LayItem, f_val) -> None:
        """一个数据格：状态四态 + 浅玻璃 + 底部值条 + S 徽章 + 列光谱底。

        渲染优先级（自上而下）：无格(N/A 或空) > 已清空 > 选中 > 悬停 > 正常。

        ★ 两种"没有格"必须分开画（否则表格会出现大片空洞，看起来像没做完）：
          · 通道区（Programmer）：该设备**没有这个通道** → 45° 斜纹 N/A（信息："无此值类型"）
          · 效果/Clip 区：该效果**不作用这台设备** → 纯平空格（信息："这行与该效果无关"）
        """
        r = it.rect
        # ★ 用布局期预解析的引用（见 _LayItem.cell/sec）：原实现在**每格**做
        #   `row.cell_of()`（线性扫 25 列）+ `model.section()`（线性扫分区），
        #   一屏 600 格 → 1.5 万次比较，是绘制耗时的隐性大头。
        cell = getattr(it, "cell", None)
        if cell is None and it.row is not None:
            cell = it.row.cell_of(it.col_key)
        sec = getattr(it, "sec", None) or self._section(it.section)
        if cell is None:
            if sec is not None and sec.key in _CHANNEL_SECTIONS:
                self._draw_na(p, r)
            else:
                self._draw_blank(p, r, it)
            return
        key = (it.row.fixture_id, cell.col_key)
        val, owned = self._values.get(key, (None, False))
        txt, state = cell_display(cell, val, owned, self._unit,
                                  spread=self._spreads.get(key))
        col = self._col_color(it.col)
        selected = key in self._selected
        hovered = (key == self._hover)

        body = r.adjusted(0, 0, 0, 0)
        if state == _STATE_NA:
            self._draw_na(p, body)
            return
        if state == _STATE_EMPTY and not selected and not cell.virtual:
            # 已清空：纯平灰底、**不画数字**（与"值0 有效果"明确区分）
            p.fillRect(body, _qhex(_th.BG_DEEP))
            p.setPen(QPen(_qhex(_th.BORDER_NORMAL), 1))
            p.drawLine(body.left(), body.bottom() - 1, body.right(), body.bottom() - 1)
            if hovered:
                p.fillRect(body, _qc(255, 255, 255, 10))
            return

        # 玻璃底：分类色纵向渐变（浅强度，保证数字对比度）
        # ★ 用缓存路径（相对坐标）+ 平移到目标位置：省掉每格重建 QPainterPath
        path = _rounded_path(body.width(), body.height(), CELL_R).translated(
            body.left(), body.top())
        if selected:
            top = QColor(_th.BRAND_ORANGE_SELECTED)
            bot = QColor(_th.BRAND_ORANGE_SELECTED); bot.setAlpha(170)
            border = QColor(_th.BRAND_ORANGE_INK)
        elif state == "mixed":
            # 聚合格内取值不一致：琥珀玻璃 + 居中「混合」（一眼区分"组内不齐"）
            top = QColor(_th.BRIGHT_WARN); top.setAlpha(46)
            bot = QColor(_th.BRIGHT_WARN); bot.setAlpha(16)
            border = QColor(_th.BRIGHT_WARN); border.setAlpha(150)
        elif state == "zero":
            top = QColor(col); top.setAlpha(22)
            bot = QColor(col); bot.setAlpha(8)
            border = QColor(col); border.setAlpha(70)
        else:
            top = QColor(col); top.setAlpha(40)
            bot = QColor(col); bot.setAlpha(16)
            border = QColor(col); border.setAlpha(120)
            if hovered:
                border = QColor(_th.BRAND_ORANGE_INK)
        grad = QLinearGradient(0, body.top(), 0, body.bottom())
        grad.setColorAt(0.0, top); grad.setColorAt(1.0, bot)
        p.fillPath(path, QBrush(grad))
        # 顶部内高光（1.5px/α18 —— 满强度只留给列头与选中态，格上要克制）
        if not selected:
            hl = QPainterPath()
            hl.addRoundedRect(QRectF(body.left() + 1.5, body.top() + 1.0,
                                     body.width() - 3, 1.8), 1.0, 1.0)
            p.fillPath(hl, QBrush(QColor(255, 255, 255, 18)))
        # 列光谱底（极淡的频道色，扫一眼定位列）
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        # 数字（右对齐；Consolas）
        # ★ 有 S 徽章时右侧必须**留出徽章宽度**，否则徽章压在数字上（实测把 "200"
        #   压成 "26S0"）。数字仍右对齐，只是右边距按需增加。
        has_s = (key in self._marks) or (key in self._panel_marks)
        if txt:
            p.setFont(f_val)
            if selected:
                p.setPen(QColor(_th.TEXT_PRIMARY))
            elif state == "mixed":
                p.setPen(QColor(_th.WARN_ORANGE))
            elif state == "zero":
                p.setPen(QColor(_th.TEXT_DISABLED))
            else:
                p.setPen(_qhex(_th.TEXT_PRIMARY))
            if state == "mixed":
                # 「混合」是**文字**不是数值 → 居中、用小一号界面字体，别当数字右对齐
                f_mix = ui_font(cell_font_px(10))
                p.setFont(f_mix)
                p.drawText(body.adjusted(4, 0, -4, -1),
                           int(Qt.AlignmentFlag.AlignCenter), txt)
            else:
                right_pad = -16 if has_s else -6
                p.drawText(body.adjusted(6, 0, right_pad, -1),
                           int(Qt.AlignmentFlag.AlignRight |
                               Qt.AlignmentFlag.AlignVCenter),
                           txt)
        # 底部值条（液态玻璃招牌；VIRTUAL 与 zero 也画，表示"有效果"）
        if val is not None and state not in (_STATE_NA, "mixed"):
            lvl = max(0.0, min(1.0, float(val) / 255.0))
            if lvl > 0:
                m = QRect(body.left() + 4, body.bottom() - VALUE_BAR_H - 2,
                          max(2, int((body.width() - 8) * lvl)), VALUE_BAR_H)
                bp = QPainterPath()
                bp.addRoundedRect(QRectF(m), 1.0, 1.0)
                fill = QColor(_th.TEXT_SELECTED_FG) if selected else QColor(col)
                if not selected:
                    fill.setAlpha(235)
                p.fillPath(bp, QBrush(fill))

        # 格线（1px，与主题 QSS gridline-color 同值）
        p.setPen(_pen_grid)
        p.drawLine(body.left(), body.bottom() - 1, body.right(), body.bottom() - 1)
        p.drawLine(body.right() - 1, body.top(), body.right() - 1, body.bottom())

        # 来源角标：**仅「按来源」分组 + 单来源**时画（默认「合并」不区分来源 → 不画）。
        srcs = self._sources.get(key, ())
        if len(srcs) == 1 and self.show_source_badge() and not it.row.selected:
            self._draw_source_badge(p, body, srcs[0])

        # 节点侧"当前选中"高亮（clip 选中）：FOCUS 描边 + 左上三角角标。
        # ★ 与调度台自身的框选**共存**（两者语义不同，故叠加而非互斥）。
        if bool(getattr(it.row, "selected", False)):
            p.setPen(QPen(_qhex(_th.FOCUS_BLUE), 1.5))
            p.drawPath(path)
            tri = QPainterPath()
            tri.moveTo(body.left() + 1.5, body.top() + 1.5)
            tri.lineTo(body.left() + 10.0, body.top() + 1.5)
            tri.lineTo(body.left() + 1.5, body.top() + 10.0)
            tri.closeSubpath()
            p.fillPath(tri, QBrush(_qhex(_th.FOCUS_BLUE)))

        # S 徽章（右上角金色胶囊）
        if has_s:
            self._draw_s_badge(p, body)

    def _draw_s_badge(self, p: QPainter, body: QRect) -> None:
        """S 徽章：右上角 10px 金色胶囊 + 深色 S（的 marked-to-save）。"""
        w, h = 11, 11
        x = body.right() - w - 2
        y = body.top() + 2
        bp = QPainterPath()
        bp.addRoundedRect(QRectF(x, y, w, h), 2.5, 2.5)
        c = QColor(_th.KEYFRAME_GOLD); c.setAlpha(235)
        p.fillPath(bp, QBrush(c))
        f = ui_font(cell_font_px(9), True)
        p.setFont(f)
        p.setPen(QColor(_th.BG_DEEP))
        p.drawText(QRect(x, y, w, h), int(Qt.AlignmentFlag.AlignCenter), "S")

    def _draw_source_badge(self, p: QPainter, body: QRect, panel_id: str) -> None:
        """来源角标：左上角 9px 圆点 + 面板单字（P/E/M/L/A/J/C）。

        ★ 用**面板 id → 单字**而不是图标：图标要资源、且在小格里难辨认；
          单字保持 克制技术感，一眼知道"这值出自哪个页面"。
        """
        ch = _SOURCE_GLYPH.get(str(panel_id), "")
        if not ch:
            return
        d = 10
        x = body.left() + 2
        y = body.top() + 2
        c = _SOURCE_COLOR.get(str(panel_id), QColor(_th.BRIGHT_INFO))
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, d, d), 2.0, 2.0)
        cc = QColor(c); cc.setAlpha(215)
        p.fillPath(path, QBrush(cc))
        f = ui_font(cell_font_px(8), True)
        p.setFont(f)
        p.setPen(QColor(_th.BG_DEEP))
        p.drawText(QRect(x, y, d, d), int(Qt.AlignmentFlag.AlignCenter), ch)

    def _draw_na(self, p: QPainter, r: QRect) -> None:
        """N/A 格：深底 + 45° 细斜纹（"此设备无该值类型"，视觉后退）。"""
        p.fillRect(r, _qhex(_th.BG_ROOT))
        p.setPen(_pen_na)
        step = 7
        for x in range(r.left() - r.height(), r.right() + 1, step):
            p.drawLine(x, r.bottom() - 1, x + r.height(), r.top())
        p.setPen(QPen(_qhex(_th.BORDER_NORMAL), 1))
        p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)

    def _draw_blank(self, p: QPainter, r: QRect, it: "_LayItem") -> None:
        """空格外（该行与该效果无关）：纯平底 + 网格线 + 每行一次的极淡斑马纹。

        刻意**不加斜纹**：斜纹表示"无此通道"（信息性），而这里只是"该效果没作用到
        这台设备"。用最轻的底色 + 网格线保持表是完整方格阵，不出现大片空洞。
        """
        row = it.row
        zebra = QColor(_th.BG_INPUT) if (row is not None and row.sub % 2) \
            else QColor(_th.BG_ROOT)
        p.fillRect(r, zebra)
        p.setPen(_pen_grid)
        p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)
        p.drawLine(r.right() - 1, r.top(), r.right() - 1, r.bottom())

    # ── 颜色 ────────────────────────────────────────────────────────────
