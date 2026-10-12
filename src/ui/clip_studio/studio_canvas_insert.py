# -*- coding: utf-8 -*-
"""边上插入卡（R183 W2-ST #11）—— 边中点热点拖出 → 断边重连两段。

交互（节点编辑器边插入惯例）：

* 左键按在边**中点热点**（半径 ``_INSERT_HOTSPOT_PX`` 屏幕像素）→ 进入插入拖动；
* 拖动超过阈值后松手：落点有卡 → 用该卡当中继（`through`）；空白 → 在落点新建
  中继卡（类型缺省 = 上游卡类型）；**未拖动** → 退化为点选该边（R172 旧语义不变）；
* 中继卡资格：有 in 与 out 桩、非便签、非禁用、不是该边两端自身；
* 改图**事务性**：两段任一建不成 → 旧边原样保留；一次操作只压一条撤销。

从 `studio_canvas_ops` 拆出（≤500 行门禁 + 函数 CC/参数门禁）；宿主 = 画布
（`_sheet/_node_by_id/_add_edge/create_node/_px/_node_at/_begin_undo_op/
_commit_graph_change/_delete_nodes/_undo_token/_notify/selection_changed/update`）。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF

from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_cards import PORT_IN, PORT_OUT

__all__ = ["EdgeInsertMixin"]

_INSERT_HOTSPOT_PX = 7.0              # 边中点热点命中半径（屏幕像素）
_INSERT_THRESHOLD_PX = 4.0            # 热点拖出判定阈值（屏幕像素）


class EdgeInsertMixin:
    """边中点插入（宿主 = ClipStudioCanvas）。"""

    # ── 热点命中 / 拖动 / 松手 ──────────────────────────────────────────
    def _edge_hotspot_at(self, wp: QPointF) -> tuple:
        """命中边中点热点 → ``(edge_id, 中点世界坐标)``；未命中 → ``("", None)``。"""
        tol = self._px(_INSERT_HOTSPOT_PX)
        for e in reversed(self._sheet["graph"].get("edges", [])):
            path = self._edge_path_of(e)
            if path is None:
                continue
            mid = path.pointAtPercent(0.5)
            dx, dy = mid.x() - wp.x(), mid.y() - wp.y()
            if dx * dx + dy * dy <= tol * tol:
                return str(e.get("id", "")), mid
        return "", None

    def _begin_insert_drag(self, eid, wp: QPointF, mid: QPointF) -> None:
        """登记插入拖动上下文（中点 + 光标 + 是否真拖出）。"""
        self._insert = {"eid": str(eid), "cur": QPointF(wp), "mid": QPointF(mid),
                        "moved": False}
        self.update()

    def _drag_insert(self, wp: QPointF) -> None:
        """插入拖动一帧：超过阈值标记 moved（松手才真正改图）。"""
        ins = self._insert
        if ins is None:
            return
        ins["cur"] = QPointF(wp)
        d = wp - ins["mid"]
        if not ins["moved"] and math.hypot(d.x(), d.y()) >= self._px(_INSERT_THRESHOLD_PX):
            ins["moved"] = True
        self.update()

    def _finish_insert(self) -> None:
        """松手：未移动 = 点选该边（保留旧语义）；移动 = 断边插入中继卡。"""
        ins = self._insert
        self._insert = None
        if ins is None:
            return
        if not ins["moved"]:
            self._clear_selection()
            self._sel_edge = str(ins["eid"])
            self.selection_changed.emit("")
            self.update()
            return
        target = self._node_at(ins["cur"])
        self.insert_node_on_edge(ins["eid"], ins["cur"],
                                 through=str(target.get("id")) if target else None)

    # ── 事务性断边重连 ─────────────────────────────────────────────────
    def insert_node_on_edge(self, eid, wp: QPointF, *, through=None,
                            new_key=None) -> dict | None:
        """断边 → 经中继卡重连两段；返回中继卡节点（失败 → None）。

        `through` = 既有卡 id（松手在卡上）；缺省 = 在 wp 新建（`new_key` 缺省 =
        上游卡类型）。失败路径不留下半成品（新建的卡回滚移除），且不产生撤销记录。
        """
        graph = self._sheet["graph"]
        edge = next((e for e in graph.get("edges", []) if str(e.get("id")) == str(eid)), None)
        if edge is None:
            return None
        up, down = self._relay_ends(edge)
        if up is None:
            return None
        mid = self._node_by_id(str(through)) if through else None
        if mid is not None and not self._can_relay(mid, edge):
            self._notify("该卡不能作为中继：需要入桩与出桩（便签/禁用卡/边两端不可）")
            return None
        return self._build_relay(edge, (up, mid), wp, new_key=new_key)

    def _relay_ends(self, edge) -> tuple:
        """边的上下游卡；任一缺失 → ``(None, None)``。"""
        up = self._node_by_id(str(edge.get("from")))
        down = self._node_by_id(str(edge.get("to")))
        return (up, down) if up is not None and down is not None else (None, None)

    def _build_relay(self, edge, spec: tuple, wp, *, new_key=None) -> dict | None:
        """压撤销 → 备中继卡 → 事务重接 → 提交（失败回滚，见 `_rollback_relay`）。

        `spec` = ``(上游卡, 既有中继或 None)``（参数收在一个元组里，守位置参数门禁）。
        """
        graph, up, mid = self._sheet["graph"], spec[0], spec[1]
        self._begin_undo_op()
        created = False
        if mid is None:
            mid = self.create_node(str(new_key or up.get("key")), QPointF(wp))
            created = True
            if mid is None:
                self._undo_token = False
                return None
        if not self._can_relay(mid, edge):
            self._rollback_relay(created, mid)
            return None
        if self._replace_edge(graph, edge, str(up.get("id")), str(mid.get("id"))) is None:
            self._notify("断边重连失败：已有同向连线或桩位不兼容")
            self._rollback_relay(created, mid)
            return None
        self._commit_graph_change()
        self.selection_changed.emit(str(mid.get("id")))
        self.update()
        return mid

    def _rollback_relay(self, created: bool, mid) -> None:
        """中继失败回滚：新建的卡移除；已压的撤销 token 清掉（不为无操作留撤销）。"""
        if created and mid is not None:
            self._delete_nodes({str(mid.get("id"))})
        self._undo_token = False

    def _can_relay(self, node, edge) -> bool:
        """中继资格：非便签/非禁用/有 in 与 out 桩/不是边两端自身。"""
        nid = str(node.get("id"))
        if nid in (str(edge.get("from")), str(edge.get("to"))):
            return False
        if node.get("disabled") or studio_note.is_note(node):
            return False
        spec = studio_note.lookup_spec(node.get("key"))
        ports = tuple(getattr(spec, "ports", ()) or ())
        return PORT_IN in ports and PORT_OUT in ports

    def _replace_edge(self, graph, edge, up_id: str, mid_id: str) -> dict | None:
        """事务性重接：删旧边 → 建 up→mid、mid→down（任一失败回滚）。"""
        edges = graph.setdefault("edges", [])
        try:
            edges.remove(edge)
        except ValueError:
            return None
        e1 = self._add_edge(str(up_id), str(mid_id), PORT_IN)
        e2 = self._add_edge(str(mid_id), str(edge.get("to")),
                            str(edge.get("to_port", PORT_IN)))
        if e1 is not None and e2 is not None:
            return e1
        for e in (e1, e2):
            if e is not None and e in edges:
                edges.remove(e)
        edges.append(edge)
        return None
