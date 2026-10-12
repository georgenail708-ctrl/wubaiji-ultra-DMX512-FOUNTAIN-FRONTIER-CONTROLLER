# -*- coding: utf-8 -*-
"""画布工具层 · 编辑操作块（R183 W2-ST #3/#4/#6/#7/#8/#9/#11/#12/#14/#17）。

从 `studio_canvas_tools` 拆出（≤500 行门禁）；基础块（撤销钩子/浮层/键位/导航/
上下文菜单）在同名模块的 `CanvasToolsMixin`，本混入 `CanvasOpsMixin` 只放具体编辑
操作，二者由宿主 `ClipStudioCanvas` 依次混入（无方法重名）。

逐项实现要点：

* **#4 连线样式**：sheet 级 `edge_style`（贝塞尔/直角/直线），`_edge_path` 是绘制/
  命中/预览唯一入口；旧档无键 → 贝塞尔（逐字节旧行为）。
* **#6/#14 折叠**：`studio_collapse` 纯函数；折叠/最小化只改 UI 键，编译器只读。
* **#7 自动布局 / #8 对齐分布**：`studio_layout` 纯函数（stdlib Kahn，无环才动）。
* **#9 搜索**：Ctrl+F 浮层 → `focus_node` + 2s 高亮。
* **#11 边上插入**：边中点热点拖出 → 断边重连两段（中继卡可新建或用既有卡）。
* **#12 流动动画**：选中/运行中边 dashOffset 推进（33ms；无选中零开销）。
* **#17 禁用**：节点 `disabled`；编译器跳过 + 灰色渲染。
* **#3 便签**：`studio_note` 伪卡（无桩不可接线，不入效果目录计数）。
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath

from src.ui.clip_studio import studio_collapse
from src.ui.clip_studio import studio_edge_style as _es
from src.ui.clip_studio import studio_layout
from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_cards import PORT_IN, PORT_OUT
from src.ui.clip_studio.studio_search import match_nodes

__all__ = ["CanvasOpsMixin"]

_EPS = 1e-9
_FLOW_PATTERN = (5.0, 4.0)            # dash 图样（笔宽为单位）
_INSERT_HOTSPOT_PX = 7.0              # 边中点热点命中半径（屏幕像素）
_LAYOUT_X_GAP = 300.0                 # 自动布局列距（≈卡宽 + 124，含连线空间）
_LAYOUT_Y_GAP = 150.0                 # 自动布局行距（≈卡高 + 46）
_HL_MS = 2000                         # 搜索命中高亮时长（任务书 2s）
_INSERT_THRESHOLD_PX = 4.0            # 热点拖出判定阈值（屏幕像素）


class CanvasOpsMixin:
    """画布编辑操作（宿主 = ClipStudioCanvas；基础块在 `studio_canvas_tools`）。"""

    # ── #4 连线样式 ────────────────────────────────────────────────────
    def edge_style(self) -> str:
        """当前 sheet 的连线样式（旧档缺键 → 贝塞尔）。"""
        return _es.edge_style_of(self._sheet)

    def set_edge_style(self, style) -> bool:
        """设连线样式并提交（真变化才发图变更 = 一条撤销/一次落盘）。"""
        if style not in _es.EDGE_STYLES or self.edge_style() == style:
            return False
        self._begin_undo_op()
        _es.set_edge_style(self._sheet, style)
        self._commit_graph_change()
        self.update()
        return True

    def cycle_edge_style(self) -> str:
        """三态循环切换（曲线 → 直角 → 直线）；返回新样式。"""
        nxt = _es.cycle_edge_style(self.edge_style())
        self.set_edge_style(nxt)
        return nxt

    def _edge_path(self, p1: QPointF, p2: QPointF, from_port: str = PORT_OUT,
                   to_port: str = PORT_IN) -> QPainterPath:
        """按当前样式生成连线路径（绘制/命中/预览/插入热点唯一入口）。"""
        return _es.seg_path(p1, p2, from_port, to_port, self.edge_style())

    @staticmethod
    def _seg_bezier(p1: QPointF, p2: QPointF,
                    from_port: str = PORT_OUT,
                    to_port: str = PORT_IN) -> QPainterPath:
        """R182 契约保留（测试/脏区按名引用）：委托 `studio_edge_style.seg_bezier`。"""
        return _es.seg_bezier(p1, p2, from_port, to_port)

    def _edge_path_of(self, edge) -> QPainterPath | None:
        """边 → 路径（端点缺失或任一端被折叠隐藏 → None）。"""
        src = self._node_by_id(str(edge.get("from")))
        dst = self._node_by_id(str(edge.get("to")))
        if src is None or dst is None:
            return None
        hidden = self._hidden_ids()
        if str(src.get("id")) in hidden or str(dst.get("id")) in hidden:
            return None
        return self._edge_path(self._port_pos(src, str(edge.get("from_port", PORT_OUT))),
                               self._port_pos(dst, str(edge.get("to_port", PORT_IN))),
                               str(edge.get("from_port", PORT_OUT)),
                               str(edge.get("to_port", PORT_IN)))

    # ── #8 对齐 / 分布 ─────────────────────────────────────────────────
    def _sel_rects(self, ids) -> list:
        """选中卡 → ``[(x, y, w, h), …]``（世界坐标；顺序同 ids）。"""
        out = []
        for nid in ids:
            node = self._node_by_id(nid)
            if node is not None:
                r = self._card_rect(node)
                out.append((r.x(), r.y(), r.width(), r.height()))
        return out

    def _apply_offsets(self, ids, deltas) -> int:
        """位移表落位（一次图变更 = 一条撤销）；返回真移动的卡数。"""
        plan = []
        for nid, (dx, dy) in zip(ids, deltas):
            node = self._node_by_id(nid)
            if node is not None and (abs(dx) > _EPS or abs(dy) > _EPS):
                plan.append((node, dx, dy))
        if not plan:
            return 0
        self._begin_undo_op()
        for node, dx, dy in plan:
            node["x"] = float(node.get("x", 0.0)) + dx
            node["y"] = float(node.get("y", 0.0)) + dy
        self._commit_graph_change()
        self.update()
        return len(plan)

    def align_selection(self, mode) -> int:
        """对齐多选集（left/right/top/bottom，Ctrl+L/R/T/B）；返回移动卡数。"""
        ids = self._visible_sel_ids()
        if len(ids) < 2 or mode not in studio_layout.ALIGN_MODES:
            if len(ids) < 2:
                self._notify("对齐需要选中 2 张以上卡片")
            return 0
        return self._apply_offsets(ids, studio_layout.align_deltas(self._sel_rects(ids), mode))

    def distribute_selection(self) -> int:
        """等距均分多选集（Ctrl+D；轴 = 包围盒长边）；返回移动卡数。"""
        ids = self._visible_sel_ids()
        if len(ids) < 3:
            self._notify("均分需要选中 3 张以上卡片")
            return 0
        rects = self._sel_rects(ids)
        return self._apply_offsets(
            ids, studio_layout.distribute_deltas(rects, studio_layout.distribute_axis(rects)))

    # ── #7 自动布局（Kahn 拓扑分层；有环提示不动）──────────────────────
    def auto_layout_cards(self, x_gap: float = _LAYOUT_X_GAP,
                          y_gap: float = _LAYOUT_Y_GAP) -> int:
        """自动布局可见卡；有环 → 提示并返回 -1（不动任何卡），否则返回移动卡数。"""
        nodes = self._visible_nodes()
        if not nodes:
            return 0
        xs = [float(n.get("x", 0.0)) for n in nodes]
        ys = [float(n.get("y", 0.0)) for n in nodes]
        plan = studio_layout.auto_layout(nodes, self._sheet["graph"].get("edges", []),
                                         x_gap, y_gap, (min(xs), min(ys)))
        if plan is None:
            self._notify("检测到环路：自动布局已取消（先解开环再布局）")
            return -1
        ids = [str(n.get("id")) for n in nodes]
        deltas = []
        for n in nodes:
            x, y = plan[str(n.get("id"))]
            deltas.append((x - float(n.get("x", 0.0)), y - float(n.get("y", 0.0))))
        return self._apply_offsets(ids, deltas)

    # ── #6/#14 折叠（子图收束 + 最小化）────────────────────────────────
    def toggle_card_collapse(self, nid) -> bool:
        """单卡折叠/展开（标题条双击 / 菜单）；返回折叠后状态。"""
        node = self._node_by_id(str(nid))
        if node is None:
            return False
        self._begin_undo_op()
        if studio_collapse.is_collapsed(node):
            studio_collapse.expand_node(node)
            folded = False
        else:
            node["collapsed"] = True
            folded = True
        self._commit_graph_change()
        self.update()
        return folded

    def collapse_selection_into_active(self) -> int:
        """把多选其余卡收束进 active 卡（Ctrl+G / 工具栏）；返回收进的子卡数。"""
        parent = str(self._sel_node or "")
        members = [i for i in self._visible_sel_ids() if i != parent]
        reason = studio_collapse.can_collapse(self._active_nodes(), parent, members)
        if reason:
            self._notify(reason)
            return 0
        self._begin_undo_op()
        studio_collapse.collapse_into(self._active_nodes(), parent, members)
        self._sel_nodes = {parent}          # 隐藏卡不得留在选中集
        self._commit_graph_change()
        self.selection_changed.emit(parent)
        self.update()
        return len(members)

    def expand_selected(self) -> int:
        """展开选中折叠卡（无选中 → 展开全部折叠卡）；返回展开数。"""
        sel = set(self._visible_sel_ids())
        targets = [n for n in self._active_nodes()
                   if studio_collapse.is_collapsed(n) and (not sel or str(n.get("id")) in sel)]
        if not targets:
            return 0
        self._begin_undo_op()
        for n in targets:
            studio_collapse.expand_node(n)
        self._commit_graph_change()
        self.update()
        return len(targets)

    # ── #17 禁用（bypass 单卡）─────────────────────────────────────────
    def set_nodes_disabled(self, nids, disabled: bool = True) -> int:
        """设卡禁用态；返回改动卡数（已是目标态不重复写 = 不发假图变更）。"""
        plan = [n for n in (self._node_by_id(i) for i in (nids or ()))
                if n is not None and bool(n.get("disabled")) != bool(disabled)]
        if not plan:
            return 0
        self._begin_undo_op()
        for n in plan:
            n["disabled"] = bool(disabled)
        self._commit_graph_change()
        self.update()
        return len(plan)

    def toggle_disabled_selection(self) -> int:
        """M：选中集禁用/启用切换（混合态 → 全部禁用）；返回改动卡数。"""
        ids = self._visible_sel_ids()
        if not ids:
            self._notify("先选中要禁用的卡片")
            return 0
        nodes = [n for n in (self._node_by_id(i) for i in ids) if n is not None]
        any_enabled = any(not n.get("disabled") for n in nodes)
        return self.set_nodes_disabled(ids, any_enabled)

    def disabled_selected(self) -> int:
        """批量禁用选中卡（菜单入口）。"""
        return self.set_nodes_disabled(self._visible_sel_ids(), True)

    # ── #3 注释便签 ────────────────────────────────────────────────────
    def add_note(self, wp: QPointF, text: str = "") -> dict | None:
        """在落点建注释便签（一次图变更）；返回新节点（失败 → None）。"""
        self._begin_undo_op()
        node = self.create_node(studio_note.NOTE_KEY, QPointF(wp))
        if node is None:
            self._undo_token = False        # 失败不提交：清 token 防误吞下一次快照
            return None
        if text:
            studio_note.set_note_text(node, text)
        self._commit_graph_change()
        self.update()
        return node

    def note_text_of(self, nid) -> str:
        """便签文本（非便签/缺失 → ""）。"""
        return studio_note.note_text(self._node_by_id(str(nid)) or {})

    def set_note_text(self, nid, text) -> bool:
        """写便签文本并提交；非便签 → False。"""
        node = self._node_by_id(str(nid))
        if node is None or not studio_note.is_note(node):
            return False
        if studio_note.note_text(node) == str(text or ""):
            return False
        self._begin_undo_op()
        studio_note.set_note_text(node, text)
        self._commit_graph_change()
        self.update()
        return True

    # ── #9 画布内搜索 ──────────────────────────────────────────────────
    def search_box(self):
        """浮层搜索框（懒建；父 = 画布，测试入口）。"""
        if self._search is None:
            from src.ui.clip_studio.studio_search import SearchBoxWidget
            self._search = SearchBoxWidget(self)
            self._search.submitted.connect(self.search_submit)
            self._search.cancelled.connect(self.close_search)
        return self._search

    def open_search(self) -> None:
        """Ctrl+F：显示并聚焦搜索框（已开 → 只聚焦）。"""
        box = self.search_box()
        self._reposition_overlays()
        box.clear()
        box.show()
        box.raise_()
        box.setFocus()

    def close_search(self) -> None:
        """Esc：收起搜索浮层（高亮保留到期自动消失）。"""
        if self._search is not None:
            self._search.hide()

    def search_submit(self) -> None:
        """Enter：按 key/名称匹配 → 聚焦首个命中 + 2s 高亮；无命中 → 提示。"""
        hits = match_nodes(self._visible_nodes(), self.search_box().text())
        self._search_hits = list(hits)
        if not hits:
            self._notify("没有匹配的卡片")
            return
        self.focus_node(hits[0])
        self.highlight_node(hits[0])

    def search_hits(self) -> list:
        """最近一次搜索的命中 id 表（测试入口）。"""
        return list(self._search_hits)

    def highlight_node(self, nid, ms: int = _HL_MS) -> None:
        """高亮某卡 `ms` 毫秒（搜索命中反馈；到时自动清除）。"""
        self._hl_nid = str(nid)
        self._hl_until = time.monotonic() + max(0.05, int(ms) / 1000.0)
        self._hl_timer.start(max(50, int(ms)))
        self.update()

    def clear_highlight(self) -> None:
        """立即清除高亮（定时到点/手动）。"""
        self._hl_timer.stop()
        self._hl_nid = ""
        self._hl_until = 0.0
        self.update()

    def highlighted_node(self) -> str:
        """当前有效高亮的卡 id（过期 → ""）。"""
        if self._hl_nid and time.monotonic() < self._hl_until:
            return self._hl_nid
        return ""

    # ── #12 边流动动画 ─────────────────────────────────────────────────
    def set_flow_running(self, on: bool) -> None:
        """「运行中」动画开关（宿主播放/烘焙时置位；无选中也会流动）。"""
        self._flow_running = bool(on)
        self._flow_key = None
        self._sync_flow_timer()
        self.update()

    def flow_edge_ids(self) -> set:
        """当前应流动的边集：运行中 = 全部；否则 = 选中边 ∪ 选中卡相连边（空 → 零开销）。"""
        edges = [e for e in self._sheet["graph"].get("edges", []) if e.get("id")]
        if self._flow_running:
            return {str(e.get("id")) for e in edges}
        ids = {str(self._sel_edge)} if self._sel_edge else set()
        sel = set(self._sel_nodes) or ({self._sel_node} if self._sel_node else set())
        if not sel:
            return ids
        hidden = self._hidden_ids()
        for e in edges:
            src, dst = str(e.get("from")), str(e.get("to"))
            if (src in sel or dst in sel) and src not in hidden and dst not in hidden:
                ids.add(str(e.get("id")))
        return ids

    def _sync_flow_timer(self) -> None:
        """按需启停流动定时器（paintEvent 每帧调用；键不变 → O(1) 早退）。"""
        if not self.isVisible():
            self._flow_timer.stop()
            self._flow_key = None
            return
        graph = self._sheet["graph"]
        key = (frozenset(self._sel_nodes), self._sel_node, self._sel_edge, self._flow_running,
               len(graph.get("nodes", [])), len(graph.get("edges", [])))
        if key == self._flow_key:
            return
        self._flow_key = key
        self._flow_ids = self.flow_edge_ids()
        if self._flow_ids and not self._flow_timer.isActive():
            self._flow_timer.start()
        elif not self._flow_ids and self._flow_timer.isActive():
            self._flow_timer.stop()

    def _tick_flow(self) -> None:
        """推进 dash phase 一帧（定时器/测试入口）。"""
        self._flow_phase = (self._flow_phase + 1.0) % 64.0
        self.update()

    def flow_phase(self) -> float:
        """当前 dash phase（测试入口）。"""
        return float(self._flow_phase)

    def flow_timer_active(self) -> bool:
        """流动定时器是否在跑（无选中应停 = 零开销）。"""
        return bool(self._flow_timer.isActive())

    def _flow_dash(self, eid) -> tuple | None:
        """该边的流动 dash ``(pattern, offset)``；不参与动画 → None。"""
        if str(eid) not in self._flow_ids:
            return None
        return (_FLOW_PATTERN, self._flow_phase)

    # ── #11 边上插入卡：热点/拖动/松手在 `studio_canvas_insert`
    #    （EdgeInsertMixin；本文件守 500 行门禁，且插入事务逻辑自带一组私有助手）
