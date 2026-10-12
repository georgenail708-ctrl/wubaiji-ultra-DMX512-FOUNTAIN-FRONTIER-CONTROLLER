# -*- coding: utf-8 -*-
"""调度台表格的**输入与命中**方法（从 `table_view` 拆出：单文件 ≤500 行）。

为什么独立成 mixin：输入（鼠标/键盘/命中测试）与绘制、布局是三件事。拆开后
`table_view` 只留数据/布局，本文件专注"用户操作 → 语义信号"的翻译。

只读宿主状态（`_cell_rects`/`_row_rects`/`_col_rects`/`_order`/`_cells`/`_selected`/
`_hover`/`_spreads`/`_values`），并在操作时改选择与发信号（`selection_changed` /
`cell_activated` / `context_menu` / `quick_edit`）。
"""
from __future__ import annotations

from typing import Optional, Set, Tuple

from PyQt6.QtCore import QPoint, QRect, Qt

from ...processing.dispatch.table_model import SUMMARY_PANEL
from .table_style import _split_col_key


class TableInputMixin:
    """输入/命中方法集合（由 `FixtureTableView` 继承）。"""

    def _cell_at(self, pos: QPoint) -> Optional[Tuple[str, str]]:
        for key, r in self._cell_rects.items():
            if r.contains(pos):
                return key
        return None

    def _row_at(self, pos: QPoint) -> Optional[str]:
        for rid, r in self._row_rects.items():
            if r.contains(pos):
                return rid
        return None

    def _col_at(self, pos: QPoint) -> Optional[str]:
        for ckey, r in self._col_rects.items():
            if r.contains(pos):
                return ckey
        return None

    def _keys_in(self, rect: QRect) -> Set[Tuple[str, str]]:
        return {k for k, r in self._cell_rects.items() if rect.intersects(r)}

    # ── 绘制 ────────────────────────────────────────────────────────────

    def mousePressEvent(self, e):  # noqa: N802
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pos = e.position().toPoint()
        mods = e.modifiers()
        if e.button() == Qt.MouseButton.RightButton:
            self._begin_quick_drag(pos)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # 列头/行头点击 → 整列/整行选择（同款）
        ckey = self._col_at(pos)
        if ckey is not None:
            self._toggle_col(ckey, mods)
            return
        rid = self._row_at(pos)
        key = self._cell_at(pos)
        if rid is not None and key is None:
            self._toggle_row(rid, mods)
            return
        if key is None:
            self._dragging = True
            self._drag_start = pos
            self._drag_current = pos
            self._drag_base = set(self._selected) if (mods & Qt.KeyboardModifier.ControlModifier) else set()
            if not (mods & Qt.KeyboardModifier.ControlModifier):
                self._selected.clear()
        else:
            if (mods & Qt.KeyboardModifier.ShiftModifier) and self._last_clicked in self._order:
                i0 = self._order.index(self._last_clicked)
                i1 = self._order.index(key)
                lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
                self._selected |= set(self._order[lo:hi + 1])
            elif mods & Qt.KeyboardModifier.ControlModifier:
                if key in self._selected:
                    self._selected.discard(key)
                else:
                    self._selected.add(key)
            else:
                self._selected = {key}
            self._last_clicked = key
            self.selection_changed.emit(set(self._selected))
        self.update()

    def _toggle_col(self, ckey: str, mods) -> None:
        """点列头 = 选中该列所有格。"""
        keys = {k for k in self._order if _split_col_key(k[1])[0] == ckey}
        if mods & Qt.KeyboardModifier.ControlModifier:
            self._selected |= keys
            self._sel_cols.add(ckey)
        else:
            self._selected = set(keys)
            self._sel_cols = {ckey}
            self._sel_rows.clear()
        self.selection_changed.emit(set(self._selected))
        self.update()

    def _toggle_row(self, rid: str, mods) -> None:
        """点行头 = 选中该行所有格。"""
        keys = set()
        for k, (sec, _cell) in self._cells.items():
            if sec is None:
                continue
            for row in sec.rows:
                if row.row_id == rid:
                    if k[0] == row.fixture_id:
                        keys.add(k)
        if mods & Qt.KeyboardModifier.ControlModifier:
            self._selected |= keys
            self._sel_rows.add(rid)
        else:
            self._selected = set(keys)
            self._sel_rows = {rid}
            self._sel_cols.clear()
        self.selection_changed.emit(set(self._selected))
        self.update()

    def _hover_tooltip(self, key) -> str:
        """hover 格的提示文本（空串 = 无提示）。

        ★ 别把"无归属"一律说成"已清空"：有**默认值**的通道（电机中位 128 等）由
        NM `_channel_defaults` 供值、不属任何层（owned=False），但那是真实输出。
        只有"无归属 **且** 值为 0"才是已清空（与 `channel_grid.is_released_channel`
        及 `table_model.cell_display` 同判据）。
        """
        if key is None:
            return ""
        sec, cell = self._cells.get(key, (None, None))
        if cell is None:
            return ""
        val, owned = self._values.get(key, (None, False))
        # ★ 只按实际输出值描述（用户定案：区分 0 的来源不影响显示逻辑）。
        owner = "有值" if val is not None else "无值"
        tag = str(getattr(cell, "tag", "") or "")
        return (f"{sec.title if sec else ''}\n"
                f"{str(getattr(cell, 'ch_type', '') or '效果')}　{tag}\n"
                f"{'值 ' + str(val) if val is not None else '无值'}　{owner}")

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        key = self._cell_at(pos)
        if key != self._hover:
            # ★ 局部重绘（性能）：hover 只影响"旧格 + 新格"两个矩形。
            #   原实现整表 `update()` → 鼠标每跨过一格就重画全部可视格
            #   （表内容可长达 14 万像素），划过时明显顿。
            old_rect = self._cell_rects.get(self._hover)
            new_rect = self._cell_rects.get(key)
            if old_rect is not None:
                self.update(old_rect)
            if new_rect is not None:
                self.update(new_rect)
            if old_rect is None and new_rect is None:
                self.update()          # 兜底：拿不到矩形（进入/离开）才整表
            self._hover = key
            self.setToolTip(self._hover_tooltip(key))
        if self._dragging:
            self._drag_current = pos
            rect = QRect(self._drag_start, self._drag_current).normalized()
            self._selected = self._drag_base | self._keys_in(rect)
            self.update()
        elif self._qdrag:
            self._apply_quick_drag(pos)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.selection_changed.emit(set(self._selected))
            self.update()
        if self._qdrag:
            self._qdrag = False
            self.selection_changed.emit(set(self._selected))
            # ★★ 2026-09-14 深查修正（未保存丢失）：拖动过程刻意不置脏（每 3px 序列化
            #   整个缓存库会把拖动拖成 PPT），但**松手时必须补一次** —— 原实现只发
            #   selection_changed（=只更新状态文字），项目脏标记从未设置 →
            #   "右拖改完直接关窗口"既无未保存提示、改动也被丢弃。
            self.edit_committed.emit()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        """双击格 → 唤起"对应页面的对应编辑界面"（用户点名，参考 ）。

        目标面板的解析优先级（单一真源在 `_target_panel_for`）：
          1. 该格声明的来源面板（`set_sources`；由混音层反查而来）
          2. 该格所在分区的面板（汇总区没有面板 → 落到 3）
          3. `programmer`（设备通道值的默认归属页）
        信号 `cell_activated` 携带 `(cell_key, panel_id)`，由面板路由打开并聚焦。
        """
        key = self._cell_at(e.position().toPoint())
        if key is None:
            return
        self.cell_activated.emit((key, self.target_panel_for(key)))

    def contextMenuEvent(self, e):  # noqa: N802
        """右键菜单：把命中上下文交给面板（菜单构造在 table_ops，保持视图只管画）。"""
        pos = e.pos()
        key = self._cell_at(pos)
        ckey = self._col_at(pos)
        rid = self._row_at(pos)
        if key is not None and key not in self._selected:
            self._selected = {key}
            self.selection_changed.emit(set(self._selected))
            self.update()
        self.context_menu.emit(
            {"cell": key, "col": ckey, "row": rid, "pos": pos},
            e.globalPos())

    # ── 右拖快速编辑 ────────────────────────────────────────────────────

    def _begin_quick_drag(self, pos: QPoint) -> None:
        """在格上按右键 → 进入快速编辑（上下拖相对增减）。"""
        key = self._cell_at(pos)
        if key is None or key not in self._selected:
            return
        self._qdrag = True
        self._qdrag_last_delta = None      # 清去重键：新拖动首帧不得被吞
        self._qdrag_origin = pos
        val = self._values.get(key, (None, False))[0]
        self._qdrag_base_value = int(val or 0)

    def _apply_quick_drag(self, pos: QPoint) -> None:
        """右拖位移 → 相对增减量（每 3px = 1 DMX），交给面板落值。

        视图只发"用户要调整多少"，不直接改层——落值走 cell_ops（单一真源）。
        """
        dy = self._qdrag_origin.y() - pos.y()      # 上拖 = 增大
        delta = int(round(dy / 3.0))
        if delta != getattr(self, "_qdrag_last_delta", None):
            self._qdrag_last_delta = delta
            self.quick_edit.emit(delta)

    # ── 键盘 ────────────────────────────────────────────────────────────

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_A and (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.select_all()
        elif e.key() == Qt.Key.Key_Escape:
            self.clear_selection()
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._relayout()

    def leaveEvent(self, e):  # noqa: N802
        if self._hover is not None:
            self._hover = None
            self.update()
