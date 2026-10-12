# -*- coding: utf-8 -*-
"""调度台表格的布局与列几何（从 `table_view` 拆出：单文件 <=500 行）。

**布局唯一真源**都在这里：`_compute_layout()` 产出全部 `_LayItem`（含 rect），
`_relayout` / 命中测试 / `paintEvent` 共用同一份结果（历史上两份布局逻辑分叉
是本项目 bug 的常见来源，故不允许第二处几何计算）。

只读宿主状态（`_model`/`_row_group`/`_width`/`_unit`），并把结果写回
`self._lay_items` / `_cells` / `_order` / `_cell_rects` / `_row_rects` / `_col_rects`。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFontMetrics

from .. import qascade_theme as _th
from ...processing.dispatch.table_model import TableRow, TableSection
from .table_style import (
    GRID_A,  # noqa: F401
    GRP_H,
    GRP_HDR_H,
    col_min_w,
    compute_scale,
    hdr_h,
    grp_hdr_h,
    row_h,
    row_hdr_max,
    set_scale,
    GRP_INDENT,
    HDR_H,
    PAD,
    ROW_H,
    SEC_GAP,
    SEC_H,
    ZEBRA_A,  # noqa: F401
    _CAT_COLOR_ALIAS,
    _LayItem,
    _SOURCE_COLOR,  # noqa: F401
    _SOURCE_GLYPH,  # noqa: F401
    _STATE_EMPTY,  # noqa: F401
    _STATE_NA,  # noqa: F401
    _split_col_key,
    mono_font,
    ui_font,
)



class TableLayoutMixin:
    """Methods moved here to keep the host file <=500 lines."""

    # ── 几何记忆化（★ 性能纪律：见文件头的复杂度说明）────────────────────
    def _geometry_valid(self) -> bool:
        """几何缓存是否仍有效（`(模型对象 is 同一, 单位, 视图宽)` 未变）。

        ★ 用"强引用 + `is` 复核"而非 `id()`：本项目有明确纪律——`id()` 会被回收
          复用，命中到**别的对象**的缓存（历史 P0 bug）。这里存模型强引用比对身份。
        """
        return (self._geom_model is self._model
                and self._geom_unit == str(self._unit)
                and self._geom_width == int(self.width()))

    def _ensure_geometry(self) -> None:
        """建立/复用几何缓存：`_row_w_cache` + `_sec_geom_cache`。"""
        if self._geometry_valid():
            return
        model = self._model
        self._geom_model = model
        self._geom_unit = str(self._unit)
        self._geom_width = int(self.width())
        self._sec_geom_cache = {}
        if model is None:
            self._row_w_cache = 60
            return
        fm = QFontMetrics(ui_font(10))
        row_w = 60
        for sec in model.sections:
            for r in sec.rows:
                row_w = max(row_w, fm.horizontalAdvance(str(r.label)) + 18)
        row_w = int(min(row_hdr_max(), max(60, row_w)))
        # ★ 2026-09-14 用户："表格做得过于小了…应该调整大一些，并且随着页面的缩放
        #   自动调整。" → 先按 1.0× 算出内容自然宽，再据**视口空位程度**推缩放
        #   （见 table_style.compute_scale），然后按缩放落地行头宽/列最小宽。
        widths = self._natural_col_widths(model, fm)
        natural = row_w + sum(sum(w.values()) for w in widths.values())
        set_scale(compute_scale(self.width(), natural))
        col_min = col_min_w()
        row_w = int(min(row_hdr_max(), max(col_min, row_w)))
        self._row_w_cache = row_w
        for sec in model.sections:
            w = {k: max(col_min, int(v)) for k, v in (widths.get(sec.key) or {}).items()}
            xs = {}
            x = PAD + self._row_w_cache
            for c in sec.columns:
                xs[c.key] = x
                x += w.get(c.key, col_min)
            self._sec_geom_cache[sec.key] = (xs, w)

    def _natural_col_widths(self, model, fm) -> dict:
        """各分区列宽（1.0× 下的自然宽）：{分区 key: {列 key: 宽}}。"""
        out = {}
        for sec in model.sections:
            widths = {}
            for c in sec.columns:
                need = int(fm.horizontalAdvance(str(c.label)) + 16)
                if self._unit == "hex":
                    need = max(38, need)
                widths[c.key] = max(44, need)
            out[sec.key] = widths
        return out

    def _row_header_width(self) -> int:
        """行头列宽 = 最长设备名（夹在 [60, 150]）。各分区共用，表格左缘才对齐。"""
        self._ensure_geometry()
        return int(getattr(self, "_row_w_cache", 60))

    def _section_geometry(self, sec: TableSection) -> Tuple[Dict[str, int], Dict[str, int]]:
        """**单分区**的 (列 x 表, 列宽带) —— 每条分区的列都从行头右侧重新排起。

        ★ 为什么必须**按分区**而不是全局一张 x 表：各分区的列集合互不相干
          （Programmer 是通道列，Effect/Matrix 是效果槽列）。若共用一张全局 x 表，
          后面的分区其列会被前面分区的列"顶"到很右边 —— 实测 Programmer 的
          第一列被推到总宽 45% 处，左边一大片死白、表格看着像没排好。
          按分区各自从 `PAD + 行头宽` 起排，每个分区都是一张自洽的小表（同理）。
        """
        self._ensure_geometry()
        cached = (getattr(self, "_sec_geom_cache", None) or {}).get(sec.key)
        if cached is not None:
            return cached
        # 兜底（缓存缺该分区：理论上不会发生，保守现算）
        fm = QFontMetrics(ui_font(10))
        widths: Dict[str, int] = {}
        for c in sec.columns:
            need = int(fm.horizontalAdvance(str(c.label)) + 16)
            if self._unit == "hex":
                need = max(38, need)
            widths[c.key] = max(44, need)
        xs: Dict[str, int] = {}
        x = PAD + self._row_header_width()
        for c in sec.columns:
            xs[c.key] = x
            x += widths.get(c.key, 44)
        return xs, widths

    def _section_content_width(self, sec: TableSection) -> int:
        """该分区所需总宽（行头 + 全部列 + 两侧留白）。"""
        _xs, widths = self._section_geometry(sec)
        return PAD * 2 + self._row_header_width() + sum(widths.values())

    def _layout_stale(self) -> bool:
        """布局缓存是否失效（模型身份 / 视图宽 / 单位 / 分组集合 变过）。"""
        return not (self._layout_model is self._model
                    and self._layout_width == int(self.width())
                    and self._layout_unit == str(self._unit)
                    and self._layout_ngroups == len(self._row_group))

    def _ensure_layout(self) -> Tuple[List[_LayItem], int, int]:
        """**布局唯一入口**：仅在失效时重算，否则复用缓存。

        ★ 性能纪律（用户报"任何操作都卡"的根治点）：
          原实现让 `paintEvent` **每次**都调 `_compute_layout()` → 每次 hover/点击/
          滚动都重建 6000 个 `_LayItem`+`QRect`（实测 43ms）。现改为：
          结构/宽度/单位变化时才重算（`_relayout` 路径），绘制只读缓存。
        """
        if not self._layout_stale():
            return self._lay_items, self._content_h, self._content_w
        items, h, w = self._compute_layout()
        self._lay_items = items
        self._content_h, self._content_w = h, w
        self._layout_model = self._model
        self._layout_width = int(self.width())
        self._layout_unit = str(self._unit)
        self._layout_ngroups = len(self._row_group)
        return items, h, w

    def _compute_layout(self) -> Tuple[List[_LayItem], int, int]:
        """计算全部布局项 + (内容高, 内容宽)。

        **唯一真源**：`_relayout` 与 `paintEvent` 都调用本函数；命中测试用其产出的
        `_cell_rects/_row_rects/_col_rects`，三处不再各算一份几何。
        """
        items: List[_LayItem] = []
        model = self._model
        if model is None or not model.sections:
            return items, PAD * 2, 0
        row_w = self._row_header_width()
        # 视图宽（至少容纳最宽的分区，避免窄窗时列被裁）
        widest = max((self._section_content_width(s) for s in model.sections),
                     default=PAD * 2 + row_w)
        total_w = max(int(self.width()), widest)
        y = PAD
        for sec in model.sections:
            # ★ 每个分区独立排布自己的列（见 _section_geometry 的说明）
            xs, widths = self._section_geometry(sec)
            sec_w = max(total_w, PAD * 2 + row_w + sum(widths.values()))
            # 分区条（模块来源，用户专属特色）
            items.append(_LayItem("section", QRect(PAD, y, sec_w - PAD * 2, SEC_H),
                                  section=sec.key, label=sec.title,
                                  count=len(sec.rows)))
            y += SEC_H
            # 列头（参数分组条 + 值类型名）——必须**先于**数据行
            if sec.columns:
                items.append(_LayItem("group_hdr",
                                      QRect(PAD, y, sec_w - PAD * 2, grp_hdr_h()),
                                      section=sec.key))
                y += grp_hdr_h()
                items.append(_LayItem("col_hdr",
                                      QRect(PAD, y, sec_w - PAD * 2, hdr_h()),
                                      section=sec.key, col=sec.columns, sec=sec))
                y += hdr_h()
            # 数据行 + **随行的分组条**
            # ★ 分组条必须**插在它所辖行的前面**，不能先把它全部画出来再画行——
            #   否则同一分区的所有组条会堆在表头下方、与行脱节（实测踩到：
            #   "主舞台/侧翼"两条挤在列头上方，看起来像没有归属）。
            if sec.columns:
                y = self._emit_rows_with_groups(items, sec, y, row_w, xs, widths,
                                                sec_w)
            else:
                for row in sec.rows:
                    items.append(_LayItem("row_hdr", QRect(PAD, y, row_w, row_h()),
                                          section=sec.key, row=row, label=row.label))
                    y += row_h()
            y += SEC_GAP
        return items, y + PAD, total_w

    def _emit_rows_with_groups(self, items, sec, y, row_w, xs, widths, sec_w):
        """逐行排布，并在**组身份变化处**插入分组条（母/子）。

        组条与行的相对位置是"标题-内容"关系，所以必须在同一趟循环里穿插；
        分组身份由 `_group_of_row` 给出（已创建组=母 depth1 / 临时选中=子 depth2）。
        clip 分区不出组条（用户明确除外）。
        """
        no_groups = (sec.key == "clip")
        cur_key = None
        for row in sec.rows:
            if not no_groups:
                gkey, gname, depth = self._group_of_row(sec, row)
                if gkey and gkey != cur_key:
                    cur_key = gkey
                    indent = GRP_INDENT if depth >= 2 else 0
                    items.append(_LayItem(
                        "group",
                        QRect(PAD + indent, y, sec_w - PAD * 2 - indent, GRP_H),
                        section=sec.key, folder=gname, depth=depth))
                    y += GRP_H
            items.append(_LayItem("row_hdr", QRect(PAD, y, row_w, row_h()),
                                  section=sec.key, row=row, label=row.label))
            for c in sec.columns:
                x = xs.get(c.key, PAD + row_w)
                w = widths.get(c.key, 44)
                # 布局期预解析格与分区（绘制热路径零查找，见 _LayItem.cell/sec）
                cell = row.cell_of(c.key)
                items.append(_LayItem("cell", QRect(x, y, w, row_h()),
                                      section=sec.key, col=c, row=row,
                                      col_key=c.key, cell=cell, sec=sec))
            y += ROW_H
        return y

    def _group_of_row(self, sec: TableSection, row: TableRow) -> Tuple[str, str, int]:
        """行的组归属：返回 (组键, 显示名, 层级)。

        组信息由调度台面板从总线节点解析后注入（`set_row_groups`），本视图只查表
        ——不自行反查设备集合（那是 group_resolver 的职责，保持零耦合）。

        层级：已创建组 = 1（母条）；仅临时选中（有 parent、无 id）= 2（子条，缩进）。
        """
        gid, gname, pid = self._row_group.get(
            row.fixture_id, ("", "", ""))[:3]
        gid = str(gid or "")
        pid = str(pid or "")
        if gid:
            return f"gid:{gid}", str(gname or gid), 1
        if pid:
            return f"pid:{pid}", str(gname or "临时选中"), 2
        return "", "", 0

    def set_row_groups(self, mapping: Dict[str, Tuple[str, str, str]],
                       defer: bool = False) -> None:
        """注入"设备 → (group_id, group_name, parent_group_id)"（由调度台面板解析后传入）。

        `defer=True`：只更新映射、**不立即重排**（供"随即还会 set_model"的调用方
        使用，避免一次 tick 内连排两遍；见 monitor_cells._derive_row_groups）。
        """
        self._row_group = dict(mapping or {})
        self._layout_ngroups = -1          # 分组集合变了 → 布局失效
        if not defer:
            self._relayout()
            self.update()

    def _relayout(self) -> None:
        """按唯一真源填缓存（格/行/列 rect + 视觉顺序），并设最小尺寸。

        ★ 重入守卫（必需，实测踩到）：
          `resizeEvent → _relayout → setMinimumHeight/Width → resizeEvent → …`
          会形成**无界递归**，最终栈溢出**硬崩**（进程直接退出，无 traceback、
          无 Python 异常可捕获）。守卫让"尺寸设置引发的二次 resize"直接返回。
        """
        if getattr(self, "_in_relayout", False):
            return
        self._in_relayout = True
        try:
            self._relayout_inner()
        finally:
            self._in_relayout = False

    def _relayout_inner(self) -> None:
        """真正的重排实现（由 `_relayout` 加守卫后调用）。"""
        # 分组集合可能刚变 → 先让几何/布局缓存失效
        self._geom_model = None
        items, content_h, content_w = self._ensure_layout()
        self._cells.clear()
        self._order = []
        self._cell_rects.clear()
        self._row_rects.clear()
        self._col_rects.clear()
        self._cell_tops = []
        model = self._model
        if model is not None:
            for sec in model.sections:
                for row in sec.rows:
                    for c in sec.columns:
                        cell = row.cell_of(c.key)
                        key = (row.fixture_id, cell.col_key if cell is not None else c.key)
                        self._cells[key] = (sec, cell)
        for it in items:
            if it.kind == "cell" and it.row is not None:
                cell = it.row.cell_of(it.col_key)
                if cell is None:
                    continue
                key = (it.row.fixture_id, cell.col_key)
                self._cell_rects[key] = it.rect
                self._order.append(key)
                # ★ 与 _order 同步的格顶坐标（y 有序）→ 绘制/取值切片用 bisect，
                #   使两者都只与「可视行数」成正比（性能纪律）
                self._cell_tops.append(it.rect.top())
            elif it.kind == "row_hdr" and it.row is not None:
                self._row_rects[it.row.row_id] = it.rect
            elif it.kind == "col_hdr" and it.col is not None:
                sec = self._section(it.section)
                if sec is None:
                    continue
                xs, widths = self._section_geometry(sec)
                for c in it.col:
                    x = xs.get(c.key, it.rect.left())
                    self._col_rects[c.key] = QRect(
                        x, it.rect.top(), widths.get(c.key, 44), it.rect.height())
        self._content_h = content_h
        self._content_w = content_w
        self.setMinimumHeight(int(content_h))
        self.setMinimumWidth(int(content_w))

    # ── 命中 ────────────────────────────────────────────────────────────

    def _section(self, key: str) -> Optional[TableSection]:
        return self._model.section(key) if self._model is not None else None

    def _col_color(self, col) -> QColor:
        """列强调色：按值类型取频道色（复用主题 CATEGORY_COLORS 唯一真源）。

        ★ 键名必须归一化：通道类型在表格里是归一化小写（r/g/b/w/dimmer），而
          `CATEGORY_COLORS` 的键是**全名**（red/green/blue/white/intensity/dimmer）。
          直接 `.get('r')` 取不到 → 所有列回退成同一个蓝，"列光谱底色"这项专属
          特色会静默失效（离屏渲染实测踩到：R/G/B 三列全蓝、无法扫列定位）。
        """
        ch = str(getattr(col, "ch_type", "") or "")
        if not ch:
            # 效果槽列无通道语义 → 用信息蓝作中性强调色
            return QColor(_th.BRIGHT_INFO)
        hexv = _th.CATEGORY_COLORS.get(ch) or _th.CATEGORY_COLORS.get(
            _CAT_COLOR_ALIAS.get(ch, ""))
        if hexv:
            return QColor(hexv)
        return QColor(_th.BRIGHT_INFO)

    def _col_sel(self, ckey: str) -> bool:
        return ckey in self._sel_cols

    def _col_has_mark(self, ckey: str) -> bool:
        """该列是否有已标记的格（列头小三角用）。"""
        prefix_hit = False
        for k in self._marks:
            if _split_col_key(k[1])[0] == ckey:
                prefix_hit = True
                break
        if not prefix_hit:
            for k in self._panel_marks:
                if _split_col_key(k[1])[0] == ckey:
                    prefix_hit = True
                    break
        return prefix_hit

    # ── 鼠标：左键选择 / 框选 ───────────────────────────────────────────
