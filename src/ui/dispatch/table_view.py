# -*- coding: utf-8 -*-
"""调度台 · 监控区 表格视图（自绘，扁平风格 + 液态玻璃）。

设计要点（用户 2026-09-13 定案）
────────────────────────────────
参照 表格视图：**行 = 设备/子设备，列 = 值类型，格 = 实时数值**，
格内 `S` = 已标记保存；单位 %/DMX/HEX；右键设值/标记/清空；右拖快速编辑。
保留本项目专属特色：**按模块子窗口来源分区** + 深色主题 + 液态玻璃。

★ 玻璃强度分层（解决"表格扁平 vs 玻璃深度"的冲突，本文件的核心视觉规则）
    承载**数字**的地方用**浅**玻璃（否则数字对比度被打死）；
    不承载数字的 chrome（列头/分组条/分区条）用**满**玻璃（液态玻璃的主视觉落点）；
    N/A 与已清空的格**纯平**（视觉后退，把注意力留给有效果的格）。
  这是"既像 表格那样密排可读、又有玻璃质感"的唯一自洽解。

★ 布局唯一真源：`_compute_layout()` 产出全部 `_LayItem`（含 rect），
  `resizeEvent` / 命中测试 / `paintEvent` **共用同一份**结果。历史上两份布局
  逻辑分叉是本项目 bug 的常见来源，故不再允许第二处几何计算。

★ 性能：全虚拟化——只绘制可视行；行高固定，滚动不重排。2000+ 行不卡。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import bisect
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient,
                         QPainter, QPainterPath, QPen)
from PyQt6.QtWidgets import QWidget

from .. import qascade_theme as _th
from ..editor_backdrop import paint_background
from .table_style import (  # noqa: F401  （常量与字体助手的共享真源）
    _LayItem,
    CELL_R,
    GRID_A,
    GRP_H,
    GRP_HDR_H,
    GRP_INDENT,
    HDR_H,
    PAD,
    SEC_GAP,
    SEC_H,
    VALUE_BAR_H,
    ZEBRA_A,
    _CAT_COLOR_ALIAS,
    _CHANNEL_SECTIONS,
    _SOURCE_COLOR,
    row_h,
    _SOURCE_GLYPH,
    _STATE_EMPTY,
    _STATE_NA,
    _split_col_key,
    mono_font,
    ui_font,
)
from .table_input import TableInputMixin
from .table_layout import TableLayoutMixin
from .table_paint import TablePaintMixin
from . import cell_sources as _cs
from . import table_repaint as _rp
from . import table_value_merge as _vm
from ...processing.dispatch.table_model import (
    CHANNEL_PANELS,
    SELECTION_PANEL,
    SUMMARY_PANEL,
    SCOPE_EFFECT_ONLY,
    SCOPE_GROUP_ALL,
    TableModel,
    TableRow,
    TableSection,
    cell_display,
)




def _slice_items(items, vis):
    """对 y 有序的 `_LayItem` 列表做二分切片，返回可视段（含跨界补偿）。

    ★ 性能纪律：绘制成本必须与「可视行数」成正比。`items` 由 `_compute_layout`
      按 y 递增产出，故可用 `bisect` 在 O(log n) 内定位首/末项。
    """
    n = len(items)
    if n == 0:
        return ()
    tops = _slice_items._tops_cache
    if tops is None or len(tops) != n or tops[1] is not items:
        tops = ([it.rect.top() for it in items], items)
        _slice_items._tops_cache = tops
    i0 = bisect.bisect_left(tops[0], vis.top())
    i1 = bisect.bisect_right(tops[0], vis.bottom())
    # 向两侧各扩 2 项，兜住"顶边跨界"的条目（行高固定，2 项足够）
    return items[max(0, i0 - 2):min(n, i1 + 2)]


_slice_items._tops_cache = None


class FixtureTableView(TableInputMixin, TableLayoutMixin, TablePaintMixin, QWidget):
    """风格表格视图（自绘）。

    公开接口（保持与旧 EffectBusView 的语义兼容，见 monitor_cells 薄壳）：
        set_model(model)        设置表格模型并重排
        set_values({key: (val, owned)})   设置格值（渲染层唯一数据入口）
        set_marks(set[key])     设置"已标记保存"的格键集
        selected_keys() / selected_cells() / selected_rows() / selected_cols()
        set_selected(keys) / select_all() / clear_selection()
    信号：selection_changed(set) / cell_activated(key) / context_menu(key, global_pos)
    """

    selection_changed = pyqtSignal(object)
    cell_activated = pyqtSignal(object)
    context_menu = pyqtSignal(object, object)
    quick_edit = pyqtSignal(int)    # 右拖快速编辑：相对增减量（落值走 cell_ops）
    edit_committed = pyqtSignal()   # ★ 编辑已提交（右拖松手）→ 面板置脏

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model: Optional[TableModel] = None
        self._values: Dict[Tuple[str, str], Tuple[Optional[int], bool]] = {}
        self._marks: Set[Tuple[str, str]] = set()
        self._panel_marks: Set[Tuple[str, str]] = set()
        #: 聚合格的取值分歧数（>1 → 「混合」）。单设备格无此项。
        self._spreads: Dict[Tuple[str, str], int] = {}
        #: 格的来源面板集合（{格键: (面板id,...)}）——来源角标 + 双击路由
        self._sources: Dict[Tuple[str, str], Tuple[str, ...]] = {}
        self._unit: str = "dmx"
        self._lay_items: List[_LayItem] = []
        #: 设备 id → (group_id, group_name, parent_group_id)：由调度台面板注入
        self._row_group: Dict[str, Tuple[str, str, str]] = {}
        #: 格键 → (section, cell)（命中与绘制用）
        self._cells: Dict[Tuple[str, str], Tuple[TableSection, Any]] = {}
        #: 视觉顺序（Shift 连选用）
        self._order: List[Tuple[str, str]] = []
        self._cell_rects: Dict[Tuple[str, str], QRect] = {}
        self._row_rects: Dict[Tuple[str, str], QRect] = {}
        self._col_rects: Dict[str, QRect] = {}
        self._content_h = PAD * 2
        self._in_relayout = False      # 重排重入守卫（见 table_layout._relayout）
        #: 布局/几何缓存（性能：绘制不再重算布局；见 table_layout._ensure_layout）
        self._layout_model = None
        self._layout_width = -1
        self._layout_unit = ""
        self._layout_ngroups = -1
        self._geom_model = None
        self._geom_unit = ""
        self._geom_width = -1
        self._row_w_cache = 60
        self._sec_geom_cache = {}
        #: 与 `_order` 同步的格顶坐标（y 有序）→ 绘制/取值 bisect 切片用
        self._cell_tops: List[int] = []
        self._content_w = 0

        # 选择
        self._selected: Set[Tuple[str, str]] = set()
        self._sel_rows: Set[str] = set()
        self._sel_cols: Set[str] = set()
        self._hover: Optional[Tuple[str, str]] = None
        self._last_clicked: Optional[Tuple[str, str]] = None

        # 框选 / 右拖快速编辑
        self._dragging = False
        self._drag_start = QPoint()
        self._drag_current = QPoint()
        self._drag_base: Set[Tuple[str, str]] = set()
        self._qdrag = False              # 右拖快速编辑中
        self._qdrag_origin = QPoint()
        self._qdrag_base_value = 0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(240)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_model(self, model: Optional[TableModel]) -> None:
        """设置表格模型并重排；保留仍存在的选择。"""
        self._model = model
        if model is not None:
            self._unit = str(getattr(model, "unit", "dmx") or "dmx")
        self._relayout()
        alive = set(self._cells.keys())
        self._selected &= alive
        self._sel_rows = {r for r in self._sel_rows if r in self._row_rects}
        self._sel_cols = {c for c in self._sel_cols if c in self._col_rects}
        self.update()

    def model(self) -> Optional[TableModel]:
        return self._model

    #: 来源角标（左上小圆）默认**不显示** —— 用户 2026-09-14："左一小圆这种东西
    #:  默认不要显示"。分区标题已经表明了来源，角标属冗余；如需打开改此常量为 True。
    SHOW_SOURCE_BADGE = False

    def show_source_badge(self) -> bool:
        """是否显示来源角标（左上小圆）。默认关（见 `SHOW_SOURCE_BADGE` 说明）。"""
        return bool(self.SHOW_SOURCE_BADGE)

    def set_values(self, values: Dict[Tuple[str, str], Tuple[Optional[int], bool]]) -> None:
        """**整体替换**格值快照（结构变化后全量刷新用；一次 update）。"""
        self._values = dict(values or {})
        self._spreads = {}
        self._sources = {}
        self.update()

    def update_values(self, values: Dict[Tuple[str, str], Tuple[Optional[int], bool]],
                      spreads: Optional[Dict[Tuple[str, str], int]] = None,
                      sources: Optional[Dict[Tuple[str, str], Tuple[str, ...]]] = None,
                      *, marks=None, panel_marks=None,
                      refreshed_keys: Optional[Set[Tuple[str, str]]] = None) -> None:
        """**合并式**批量刷新（每 tick 用；只 `update()` 一次）。

        ★ 性能修复（关键）：原先 `set_values`/`set_spreads`/`set_sources`/`set_marks`
          各自 `update()` → 一次 tick 触发 **4 次全量重绘**。合并成单一入口后只 1 次。
        ★ 合并而非替换：值刷新可只覆盖**可视范围**（见 panel `_refresh_values`），
          因此不能把离屏格的值清掉。

        ★★ `refreshed_keys`（本次**实际重读**的格键集）出现时，`_spreads` / `_sources`
          对这批键改为**权威替换**（缺失即删除）—— 否则"展开条带 → 混合"之后收回
          条带（或效果暂停、值回落一致）会永远停在「混合」（用户 2026-09-14 实报）。
        ★★ 重绘收窄：只重画**真正变化**的格（并集矩形）；拖动一次只动几十格。

        合并/比对逻辑在 `table_value_merge` / `table_repaint`（纯函数，可单测），
        本方法只做编排。

        ★★ 零重绘短路（2026-09-14 实测的性能回归）：`dirty_rect_for` 用同一个
          `None` 同时表达"未收窄"与"零变化"，原实现一律当"未收窄"→ 快表（100ms）
          一字未变的 tick 也整视口重绘。实测 idle tick p50：98 格表 10.2ms、360 格
          表 31.4ms，而 1 格真变化仅 0.32ms（**贵 30~90 倍且每 tick 都付**）。
          现改用 `table_repaint.plan_update` 三分：零变化时完全不调用 `update()`。
        """
        # 脏区必须在**合并之前**算（依赖旧值做比较）
        need, dirty = _rp.plan_update({"values": values, "spreads": spreads},
                                      {"values": self._values, "spreads": self._spreads},
                                      self._cell_rects, refreshed_keys=refreshed_keys)
        _vm.merge_value_caches(
            {"values": self._values, "spreads": self._spreads, "sources": self._sources},
            {"values": values, "spreads": spreads, "sources": sources},
            refreshed_keys=refreshed_keys)
        self._marks, m_changed = _vm.apply_mark_set(self._marks, marks)
        self._panel_marks, pm_changed = _vm.apply_mark_set(self._panel_marks, panel_marks)
        # 来源只喂"来源角标"（默认关，见 SHOW_SOURCE_BADGE）；开启时保守重绘防角标走旧。
        if self.show_source_badge() and sources:
            need = True
        rect = _rp.update_scope(need, dirty, m_changed or pm_changed,
                                viewport_rect=self.viewport_rect)
        if rect is not None:
            self.update(rect)

    def clear_value_caches(self) -> None:
        """清空值/分歧/来源缓存（结构重建时用，防止旧键残留）。"""
        self._values.clear()
        self._spreads.clear()
        self._sources.clear()

    def set_spreads(self, spreads: Dict[Tuple[str, str], int]) -> None:
        """设置聚合格的取值分歧数（>1 → 显示「混合」；单设备格无此项）。"""
        self._spreads = dict(spreads or {})
        self.update()

    def set_sources(self, sources: Dict[Tuple[str, str], Tuple[str, ...]]) -> None:
        """设置格的来源面板（"这值是谁写的"）：单来源显示角标，双击跳该面板。"""
        self._sources = dict(sources or {})
        self.update()

    def sources_of(self, key: Tuple[str, str]) -> Tuple[str, ...]:
        return tuple(self._sources.get(key, ()))

    def set_marks(self, marks) -> None:
        self._marks = set(marks or set())
        self.update()

    def set_panel_marks(self, marks) -> None:
        """面板侧（Programmer）自带的 S 标记（与本会话标记取或）。"""
        self._panel_marks = set(marks or set())
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = str(unit or "dmx")
        if self._model is not None:
            self._model.unit = self._unit
        self.update()

    def unit(self) -> str:
        return self._unit

    def content_height(self) -> int:
        return int(self._content_h)

    def content_width(self) -> int:
        return int(self._content_w)

    # ── 选择 ────────────────────────────────────────────────────────────
    def selected_keys(self) -> Set[Tuple[str, str]]:
        return set(self._selected)

    def selected_cells(self) -> List[Tuple[str, str]]:
        """选中的格键（按视觉顺序）。"""
        return [k for k in self._order if k in self._selected]

    def selected_rows(self) -> Set[str]:
        return set(self._sel_rows)

    def selected_cols(self) -> Set[str]:
        return set(self._sel_cols)

    def set_selected(self, keys) -> None:
        self._selected = set(keys or set())
        self.update()

    def clear_selection(self) -> None:
        self._selected.clear()
        self._sel_rows.clear()
        self._sel_cols.clear()
        self.selection_changed.emit(set())
        self.update()

    def select_all(self) -> None:
        self._selected = set(self._order)
        self.selection_changed.emit(set(self._selected))
        self.update()

    def selected_section_cells(self, section_key: str) -> List[Tuple[str, str]]:
        """某分区内被选中的格键（工具栏动作按分区路由用）。"""
        keys = set(self.selected_cells())
        out = []
        for k in self._order:
            if k not in keys:
                continue
            sec = self._cells.get(k, (None, None))[0]
            if sec is not None and sec.key == section_key:
                out.append(k)
        return out

    # ── 可视区切片（★ 性能纪律：绘制与取值都只与「可视行数」成正比）──────
    def viewport_rect(self) -> QRect:
        """**真实可视窗口**在本控件坐标系里的矩形。

        ★★ 这是本性能问题最隐蔽的一环（实测踩到，务必保留本方法）：
          本控件被放进 `QScrollArea`，`setMinimumHeight(内容高)` 让它**长到全部内容**
          （实测 view.height()=14671）。因此 `self.rect()` 是**全内容矩形**、不是可视
          窗口——若拿它当"可视区"，切片永远等于全部格（优化形同虚设，用户仍卡）。
          可视窗口是 **sheet 的 viewport**，须 `mapFrom` 到本控件坐标。
        """
        parent = self.parentWidget()
        try:
            if parent is not None and parent.height() > 0:
                return QRect(self.mapFrom(parent, QPoint(0, 0)), parent.size())
        except Exception:
            pass
        return self.rect()

    def _visible_index_range(self, pad_rows: int = 1):
        """可视区在 `_order`/`_cell_tops` 上的 `[i0, i1)` 下标区间（bisect，O(log n)）。"""
        n = len(self._order)
        if n == 0:
            return 0, 0
        tops = self._cell_tops
        if len(tops) != n:
            return 0, n
        vis = self.viewport_rect()
        lo = vis.top() - row_h() * (1 + int(pad_rows))
        hi = vis.bottom() + row_h() * (1 + int(pad_rows))
        i0 = bisect.bisect_left(tops, lo)
        i1 = bisect.bisect_right(tops, hi)
        return max(0, i0), min(n, i1)

    def visible_cell_keys(self, pad_rows: int = 1):
        """可视区（含上下各 pad_rows 行缓冲）内的格键列表（y 有序）。"""
        i0, i1 = self._visible_index_range(pad_rows)
        return self._order[i0:i1]

    def visible_cells(self, pad_rows: int = 1):
        """可视区内的 `TableCell` 对象列表（取值只针对它们）。"""
        out = []
        for k in self.visible_cell_keys(pad_rows):
            cell = self._cells.get(k, (None, None))[1]
            if cell is not None:
                out.append(cell)
        return out

    def all_cells(self):
        """全部 `TableCell`（全量兜底刷新用；低频）。"""
        out = []
        for k in self._order:
            cell = self._cells.get(k, (None, None))[1]
            if cell is not None:
                out.append(cell)
        return out

    # ── 布局（唯一真源）─────────────────────────────────────────────────
    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        try:
            self._paint(p, ev.rect())
        finally:
            # ★ 必须保证 `p.end()`：绘制过程中任何异常若逃出去，painter 会残留
            #   （Qt 报 "endPaint() called with active painter"）并污染后续重绘。
            p.end()

    def _paint(self, p: QPainter, dirty: QRect) -> None:
        """实际绘制（`paintEvent` 只负责开/关 painter 的异常安全）。"""
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_background(p, self, QColor(_th.BG_ROOT), dirty)

        f_grp = ui_font(9)
        fonts = {"sec": ui_font(11, True), "grp": f_grp, "hdr": ui_font(10),
                 "row": ui_font(10), "val": mono_font(11, True)}

        if self._model is None or not self._model.sections:
            p.setFont(f_grp); p.setPen(QColor(_th.TEXT_DISABLED))
            p.drawText(self.rect().adjusted(0, 24, 0, 0),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                       "当前没有正在制作的效果\n\n"
                       "在 Programmer / 效果预设库 / 矩阵 等页面\n"
                       "对设备（含临时选中）施加效果后，这里会按"
                       "「设备 × 值类型」成表")
            return

        # ★ 性能修复：**不再重算布局**（原实现每次都调 `_compute_layout()` →
        #   每次交互重建 6000 个 _LayItem+QRect，实测 43ms，是"任何操作都卡"的主因）。
        #   布局只在结构/宽度/单位变化时由 `_relayout` 重算并缓存，这里只读。
        items, _h, _w = self._ensure_layout()
        # ★ 切片绘制：`_lay_items` 已按 y 有序 → 只遍历可视段（约数十项）。
        #   `vis` 用**真实视口**（见 viewport_rect 的说明）∩ 本次脏区，
        #   这样单格值刷新（update(cell_rect)）只需重画该格所在行带，
        #   而不是整个视口 —— 拖动实时编辑时几十倍的绘制量差。
        #   `viewport_rect()` 只算一次（它含 mapFrom，实测 2.2us/次；
        #   原实现每帧算 2~3 次，属无谓重复）。
        vp = self.viewport_rect()
        vis = vp.intersected(dirty)
        if vis.isEmpty():
            vis = vp
        self._draw_slice(p, _slice_items(items, vis.adjusted(0, -row_h(), 0, row_h())),
                         dirty, fonts)

        # 框选矩形（FOCUS_BLUE 虚线 + 半透明填充，与监视器同款）
        if self._dragging:
            rect = QRect(self._drag_start, self._drag_current).normalized()
            p.setPen(QPen(QColor(_th.FOCUS_BLUE), 1, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(113, 168, 255, 35)))
            p.drawRect(rect)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_slice(self, p, items, dirty, fonts: dict) -> None:
        """绘制可视段内的布局项。

        `dirty == self.rect()`（整表重绘）时逐项直绘；**局部重绘**时只画与脏区
        相交的格，省掉整行带的无效绘制 —— 这是"拖动实时编辑流畅"的第二半
        （第一半是 `update(dirty_rect)` 收窄重绘区域）。

        `fonts` = {"sec","grp","hdr","row","val"}（`paintEvent` 建一次，逐项复用）。
        """
        clipped = dirty != self.rect()
        for it in items:
            spec = _ITEM_DRAW.get(it.kind)
            if spec is None:
                continue
            fn_name, font_keys, need_clip = spec
            if clipped and need_clip and not it.rect.intersects(dirty):
                continue
            getattr(self, fn_name)(p, it, *(fonts[k] for k in font_keys))

    # ── 绘制：chrome（满玻璃）────────────────────────────────────────────
    def target_panel_for(self, key: Tuple[str, str]) -> str:
        """该格双击应打开哪个面板（★ R138：多来源时先选**真效果**，见 `cell_sources`）。"""
        # ★ R194/S2-03：return 后的旧回落实现已删（R138 改造遗留的死代码尸块），
        #   真实路由由 `_cs.pick_target_panel` 承担。
        return _cs.pick_target_panel(self, key, self._sources.get(key, ()))

#: 布局项 kind → (绘制方法名, 需要传入的字体键元组, 局部重绘时是否需与脏区相交才画)
#: 「格/行列头」需要相交检查（可能只重画一个格）；分区/分组条是横贯整宽的背景，
#: 只要落在可视段内就必须画（否则局部重绘后背景缺失，出现空洞）。
_ITEM_DRAW = {
    "section": ("_draw_section", ("sec", "grp"), False),
    "group": ("_draw_group", ("grp",), False),
    "group_hdr": ("_draw_group_header", ("grp",), False),
    "col_hdr": ("_draw_col_header", ("hdr",), True),
    "row_hdr": ("_draw_row_header", ("row",), True),
    "cell": ("_draw_cell", ("val",), True),
}


__all__ = ["FixtureTableView"]
