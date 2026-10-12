# -*- coding: utf-8 -*-
"""Draw 画布撤销（R183 4.1）—— QUndoStack 命令族（路线 A，独立于工程撤销栈）。

粒度照标准命令模式（r183-candidates §二粒度表）：增删卡/
增删边 = 结构单命令；滑杆/节点拖动 = **press 记 old、release push 一条**，
`id()+mergeWith` 合并 250ms 内同键连拖；cycle/toggle/波形 = 双态单命令；
Del 多删 = `_NodesRemovedCmd` 单命令（同款，一次 undo 全回，与
beginMacro 宏语义等价）。QUndoStack.push 自动执行 redo（Qt 文档），故结构命令
的 redo 一律**幂等**（先查 id 在不在图里——push 前原路径已改图，redo 空转）。

路线取舍（详见 ai-memory/_references/undo-adoption.md §二）：Draw 图是**未应用**
的画布本地态，只有「应用」才进工程 `audio_modulation_state`（DOMAIN_TIMELINE），
不走 project_manager 域快照——避免污染共享状态与主窗撤销串域；与既有体系的一致
性取语义（双态记录、写方负责快照、undo 置脏不自动 apply），不取机制。

本混入同时收编画布的三处值变更实现（`_drag_slider`/`_toggle_target`/
`_cycle_control`，自 draw_canvas.py 迁入，行为不变）——画布文件行数门禁已满
（500），宿主 DrawCanvas 只留薄钩子。
"""
from __future__ import annotations

import copy
import time

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QUndoCommand, QUndoStack

from ..main_window_shortcuts import _text_focus
from . import draw_graph as dc
from .draw_canvas_edit import materialize_params

#: 同键连拖的合并窗口（秒）——release 间隔内连续拖算一次编辑
_MERGE_WINDOW_S = 0.25
#: `_NodeStateCmd` 的 Qt 命令族 id（非 -1 才参与 mergeWith；具体键看 merge_key）
_CMD_ID_NODE_STATE = 18341


def snap_node(node) -> dict:
    """节点深快照（params/ch_types 是嵌套可变件，浅拷会被后续拖动污染 old 态）。"""
    return copy.deepcopy(node)


def _mirror_legacy_fields(node, params: dict) -> None:
    """滑杆改值后的旧字段镜像（旧消费方/指纹读取 amount/wave_freq）。"""
    node["amount"] = float(params.get("depth", 1.0))
    node["wave_freq"] = float(params.get("freq", 2.0))


def _apply_slider_value(params: dict, key: str, frac: float) -> None:
    """滑杆 frac → 参数值（各键量纲映射；R183 3.4b 增 spread_deg 0..360 整数度）。"""
    if key == "freq":
        params["freq"] = 0.25 + frac * 7.75        # 0.25..8 周期
    elif key == "spread_deg":
        params[key] = round(frac * 360.0)          # 0..360°（整数度）
    elif key in ("attack", "release"):
        params[key] = frac * 2.0                   # 0..2 秒
    else:
        params[key] = frac                         # depth 0..1


# ══════════════════════════════════════════════════════════════════════════
#  命令族（双态：构造时存 old/new 快照；redo/undo 只做图变更 + 发信号）
# ══════════════════════════════════════════════════════════════════════════

class _NodeStateCmd(QUndoCommand):
    """整卡双态命令（滑杆/拖动/cycle/toggle/波形）：old/new 各存一份节点 dict。

    merge_key 非 None 时参与合并：同键且距上次 push ≤250ms → 只留最早 old、
    最新 new（滑杆 3 连拖 = 1 条）。
    """

    def __init__(self, canvas, nid, old_state, new_state, *,
                 text: str = "", merge_key=None):
        super().__init__(text)
        self._cv = canvas
        self._nid = str(nid)
        self._old = snap_node(old_state)
        self._new = snap_node(new_state)
        self._mk = merge_key
        self._born = time.monotonic()

    def id(self) -> int:
        return _CMD_ID_NODE_STATE if self._mk is not None else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, _NodeStateCmd) or other._mk != self._mk:
            return False
        if other._born - self._born > _MERGE_WINDOW_S:
            return False
        self._new = other._new          # 最早 old + 最新 new（首尾即全程）
        self._born = other._born        # 连拖链按最后拍续窗
        return True

    def _apply(self, state: dict) -> None:
        node = dc.node_by_id(self._cv._graph, self._nid)
        if node is None:
            return                      # 卡已被后删命令移除 → 交给那条命令
        node.clear()
        # ★ R183 清查②①F5（P2）：state 里嵌套 params 深拷——修前浅 update
        #   与活节点共享 params，下一次滑杆/菜单就地改写直接污染本命令 _new
        #   （多步 undo 后 redo 显示从未存在的中间态）。
        node.update(copy.deepcopy(state))
        self._cv.graph_changed.emit()
        self._cv.update()

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)


class _NodeAddedCmd(QUndoCommand):
    """增卡命令：redo=重插卡（连带 undo 时摘走的边）；undo=摘卡+暂存相连边。"""

    def __init__(self, canvas, node, text: str):
        super().__init__(text)
        self._cv = canvas
        self._nid = str(node.get("id", ""))
        self._snap = snap_node(node)
        self._edges: list = []          # undo 摘走的相连边（redo 回挂）

    def _emit(self) -> None:
        self._cv.graph_changed.emit()
        self._cv.update()

    def redo(self) -> None:
        graph = self._cv._graph
        if dc.node_by_id(graph, self._nid) is not None:
            return                      # push 前原路径已插 → 幂等空转
        graph["nodes"].append(dict(self._snap))
        for e in self._edges:
            if not any(str(x.get("id", "")) == str(e.get("id", ""))
                       for x in graph["edges"]):
                graph["edges"].append(dict(e))
        self._edges = []
        self._emit()

    def undo(self) -> None:
        graph = self._cv._graph
        graph["nodes"] = [n for n in (graph.get("nodes") or [])
                          if str(n.get("id", "")) != self._nid]
        self._edges = [e for e in (graph.get("edges") or [])
                       if str(e.get("from", "")) == self._nid
                       or str(e.get("to", "")) == self._nid]
        graph["edges"] = [e for e in (graph.get("edges") or [])
                          if str(e.get("from", "")) != self._nid
                          and str(e.get("to", "")) != self._nid]
        self._cv._sel_ids.discard(self._nid)
        self._emit()


class _NodesRemovedCmd(QUndoCommand):
    """多删命令（Del）：被删 nodes+edges 快照单命令，一次 undo 全回（宏等价）。"""

    def __init__(self, canvas, nodes, edges, text: str):
        super().__init__(text)
        self._cv = canvas
        self._nodes = [snap_node(n) for n in nodes]
        self._edges = [dict(e) for e in edges]
        self._nids = {str(n.get("id", "")) for n in self._nodes}

    def _drop(self) -> None:
        graph = self._cv._graph
        graph["nodes"] = [n for n in (graph.get("nodes") or [])
                          if str(n.get("id", "")) not in self._nids]
        graph["edges"] = [e for e in (graph.get("edges") or [])
                          if str(e.get("from", "")) not in self._nids
                          and str(e.get("to", "")) not in self._nids]
        for nid in self._nids:
            self._cv._sel_ids.discard(nid)

    def redo(self) -> None:
        before = len(self._cv._graph.get("nodes") or [])
        self._drop()
        if len(self._cv._graph.get("nodes") or []) != before:
            self._cv.graph_changed.emit()
            self._cv.update()

    def undo(self) -> None:
        graph = self._cv._graph
        have_n = {str(n.get("id", "")) for n in (graph.get("nodes") or [])}
        have_e = {str(e.get("id", "")) for e in (graph.get("edges") or [])}
        graph["nodes"] = graph.get("nodes") or []
        graph["edges"] = graph.get("edges") or []
        graph["nodes"] += [dict(n) for n in self._nodes
                           if str(n.get("id", "")) not in have_n]
        graph["edges"] += [dict(e) for e in self._edges
                           if str(e.get("id", "")) not in have_e]
        self._cv.graph_changed.emit()
        self._cv.update()


class _EdgeAddedCmd(QUndoCommand):
    """增边命令（拖线）：redo=重插；undo=摘边。"""

    def __init__(self, canvas, edge, text: str):
        super().__init__(text)
        self._cv = canvas
        self._snap = dict(edge)

    def redo(self) -> None:
        edges = self._cv._graph.get("edges") or []
        if any(str(e.get("id", "")) == str(self._snap.get("id", ""))
               for e in edges):
            return                      # push 前原路径已插 → 幂等空转
        self._cv._graph.setdefault("edges", []).append(dict(self._snap))
        self._cv.graph_changed.emit()
        self._cv.update()

    def undo(self) -> None:
        eid = str(self._snap.get("id", ""))
        graph = self._cv._graph
        graph["edges"] = [e for e in (graph.get("edges") or [])
                          if str(e.get("id", "")) != eid]
        self._cv.graph_changed.emit()
        self._cv.update()


# ══════════════════════════════════════════════════════════════════════════
#  DrawUndoMixin（宿主 DrawCanvas；鸭子协作：_graph/_sel_ids/graph_changed）
# ══════════════════════════════════════════════════════════════════════════

class DrawUndoMixin:
    """画布撤销混入：栈管理 + 各交互的 push 钩子 + 值变更实现（自 canvas 迁入）。"""

    def _init_undo(self) -> None:
        """建栈并联动 `undo_redo_state_changed`（宿主 QWidget 信号）。"""
        self._undo = QUndoStack(self)
        self._undo.canUndoChanged.connect(lambda _b: self._emit_undo_state())
        self._undo.canRedoChanged.connect(lambda _b: self._emit_undo_state())

    def _emit_undo_state(self) -> None:
        try:   # 画布先行析构时栈仍可能发末次联动（PyQt wrapped object 已删）
            self.undo_redo_state_changed.emit(self._undo.canUndo(),
                                              self._undo.canRedo())
        except RuntimeError:
            pass

    def _undo_reset(self) -> None:
        """文档整体替换（set_graph/clear_graph）→ 清栈（不当作可撤销步骤）。"""
        self._undo.clear()

    def undo(self) -> None:
        if self._undo.canUndo():
            from .draw_properties_ui import discard_properties
            discard_properties(self)
            self._undo.undo()           # 命令体内已发 graph_changed
            self.update()

    def redo(self) -> None:
        if self._undo.canRedo():
            from .draw_properties_ui import discard_properties
            discard_properties(self)
            self._undo.redo()
            self.update()

    # ── push 钩子（canvas 变更点调用；结构 push 前原路径已改图）──────────
    def _undo_push_add(self, node) -> None:
        self._undo.push(_NodeAddedCmd(self, node, "添加卡片"))

    def _undo_push_edge(self, edge) -> None:
        self._undo.push(_EdgeAddedCmd(self, edge, "连线"))

    def _undo_push_node_state(self, nid, old_state, text: str = "",
                              merge_key=None) -> None:
        """双态 push：old 由调用方在**变更前**捕获，new 就地取当前卡。"""
        node = dc.node_by_id(self._graph, str(nid))
        if node is None:
            return
        self._undo.push(_NodeStateCmd(self, nid, old_state, node,
                                      text=text, merge_key=merge_key))

    def _undo_push_slider(self) -> None:
        """滑杆 release：press 暂存的 old 出栈成一条命令（同键 250ms 合并）。

        ★ R183 清查④④（P2）：无位移也 push 一条 old==new 的空命令——
          第一次 Ctrl+Z 看似无效（属性变更命令有相等守卫，
          照抄口径）。
        """
        spec = self._slider
        old = spec.get("old") if spec else None
        if old is None:
            return                      # 程序化直调 _drag_slider（无 press）不进栈
        node = dc.node_by_id(self._graph, spec["nid"])
        if node is not None and node == old:
            spec.pop("old", None)       # 值未变 → 不入栈（相等守卫）
            return
        self._undo_push_node_state(
            spec["nid"], old, "调参数",
            merge_key=("param", str(spec["nid"]), str(spec["key"])))
        spec.pop("old", None)

    def _undo_push_move(self) -> None:
        """卡片 release：press 暂存的 old 出栈成一条命令（同卡 250ms 合并）。"""
        d = self._drag
        if not d or not d.get("moved") or d.get("old") is None:
            return
        self._undo_push_node_state(d["nid"], d["old"], "移动卡片",
                                   merge_key=("move", str(d["nid"])))

    def _delete_selected(self) -> None:
        """Del 多删：捕获被删 nodes+edges → 单命令入栈（一次 undo 全回）。"""
        nids = {str(n) for n in (self._sel_ids or set()) if str(n)}
        if not nids:
            return
        graph = self._graph
        victims = [snap_node(n) for n in (graph.get("nodes") or [])
                   if str(n.get("id", "")) in nids]
        edges = [dict(e) for e in (graph.get("edges") or [])
                 if str(e.get("from", "")) in nids
                 or str(e.get("to", "")) in nids]
        if not victims:
            return
        dc.delete_nodes(graph, nids)
        self._sel_ids = set()
        self.selection_changed.emit("")
        self.graph_changed.emit()
        self._undo.push(_NodesRemovedCmd(self, victims, edges,
                                         f"删除 {len(victims)} 张卡片"))
        self.update()

    # ── 值变更实现（自 draw_canvas.py 迁入，行为不变 + undo 快照）────────
    def _toggle_target(self, node, ct: str) -> None:
        """目标通道勾选切换（点 chip 即改；空目标卡可被编译丢弃）。"""
        from .draw_canvas_edit import toggle_target
        old = snap_node(node)
        toggle_target(node, ct)
        self._undo_push_node_state(str(node.get("id", "")), old, "目标通道")
        self.update()

    def _cycle_control(self, node, key: str) -> None:
        """就地循环步进一个控件值（双击；每次步进 = 一条独立命令）。"""
        from .draw_canvas_edit import cycle_control
        old = snap_node(node)
        cycle_control(node, key)
        self._undo_push_node_state(str(node.get("id", "")), old,
                                   "步进参数")
        self.update()

    def _drag_slider(self, wp) -> None:
        """滑杆拖动改值（旧档卡首拖 materialize params；release 由钩子入栈）。"""
        spec = self._slider
        x0, _y0, x1, _y1 = spec["rect"]
        frac = 0.0 if x1 <= x0 else max(0.0, min(1.0, (wp.x() - x0) / (x1 - x0)))
        node = dc.node_by_id(self._graph, spec["nid"])
        if node is None:
            return
        key = spec["key"]
        # ★ R183 清查③②C（P2）：materialize 单源化（修前与 cycle_control 双份
        #   实现漂移已发生）+ freq 钳制统一到滑杆域 0.25..8（修前 16：旧档
        #   wave_freq>8 显示顶格实际编译 12~16，首拖静默改写）。
        params = materialize_params(node)
        _apply_slider_value(params, key, frac)
        _mirror_legacy_fields(node, params)
        self.update()

    # ── 键盘路由（draw_canvas_keys 调；守卫照 main_window_shortcuts 先例）──
    def _undo_key(self, ev) -> bool:
        """Ctrl+Z=撤销 / Ctrl+Y、Ctrl+Shift+Z=重做；文本框聚焦时不抢。"""
        if _text_focus():
            return False
        mods = ev.modifiers()
        if not (mods & Qt.KeyboardModifier.ControlModifier):
            return False
        key = ev.key()
        if key == Qt.Key.Key_Z and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.undo()
            return True
        if key in (Qt.Key.Key_Y, Qt.Key.Key_Z):
            self.redo()
            return True
        return False

    def _shortcut_override(self, ev) -> bool:
        """画布接管自身撤销与Delete，避免主窗先删其它编辑对象。"""
        if ev.type() != QEvent.Type.ShortcutOverride or _text_focus():
            return False
        mods = ev.modifiers()
        if ((mods & Qt.KeyboardModifier.ControlModifier
                and ev.key() in (Qt.Key.Key_Z, Qt.Key.Key_Y))
                or (mods == Qt.KeyboardModifier.NoModifier and ev.key() == Qt.Key.Key_Delete)):
            ev.accept()
            return True
        return False
