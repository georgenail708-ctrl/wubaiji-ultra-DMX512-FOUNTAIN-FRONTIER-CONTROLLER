# -*- coding: utf-8 -*-
"""拖动脏区计算（性能红线：拖动不整图重绘）—— 从 `studio_canvas_interact` 拆出。

R183 深查② #6 新增分组框拖动后，卡片拖动 / 整组拖动 / 框拖动三处都要"节点 + 相连边"
的屏幕脏区，本模块是唯一实现点（三处不再各写一套，防几何漂移）。

宿主（ClipStudioCanvas）提供：`_node_by_id/_card_rect/_port_pos/_world_to_screen/
_sheet`（鸭子类型）。

鼓包近似见 `_edge_bulge_rect`：漏掉鼓包 = 长边拖端点时卡片外弧段残留旧像。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QRectF

from src.ui.clip_studio.studio_cards import PORT_IN, PORT_OUT

__all__ = ["DirtyRegionMixin", "BULGE_PAD", "_BULGE_PAD"]

#: 相连边脏区外扩（贝塞尔余量）：0.30 覆盖曲线超调 + 发丝线宽 + 桩环半径，宁多勿漏。
BULGE_PAD = 0.30

#: 旧名兼容（R182 测试 `test_edge_bulge_rect_pad_is_conservative` 从
#: `studio_canvas_interact` import 该名；拆分后在此保留别名）。
_BULGE_PAD = BULGE_PAD


class DirtyRegionMixin:
    """脏区计算（卡 / 组 / 框拖动共用）。"""

    def _dirty_rect_for(self, nid: str) -> QRectF:
        """节点 + 相连边（含贝塞尔鼓包）bbox → 屏幕坐标脏区。

        鼓包近似见 `_edge_bulge_rect`：漏掉鼓包 = 长边拖端点时卡片外弧段残留旧像。"""
        node = self._node_by_id(nid)
        world = self._card_rect(node) if node is not None else QRectF()
        for e in self._sheet["graph"].get("edges", []):
            if str(e.get("from")) != nid and str(e.get("to")) != nid:
                continue
            other = self._node_by_id(str(e.get("to") if str(e.get("from")) == nid
                                         else e.get("from")))
            if other is None:
                continue
            world = world.united(self._card_rect(other))
            if node is not None:
                world = world.united(self._edge_bulge_rect(e, nid, node, other))
        tl = self._world_to_screen(world.topLeft())
        br = self._world_to_screen(world.bottomRight())
        return QRectF(tl, br).normalized().toAlignedRect()

    def _edge_bulge_rect(self, e: dict, nid: str, node, other) -> QRectF:
        """相连边的贝塞尔包围矩形（脏区用）。

        R182 S 曲线恒在两端点 y 区间内（控制点与端点同 y），水平方向也只在
        |dx| 内 —— 对 QRectF(p1,p2) 四周外扩 `BULGE_PAD * dist` 是**保守余量**
        （0.30 覆盖曲线超调 + 发丝线宽 + 桩环半径，宁多勿漏）。"""
        if str(e.get("from")) == nid:
            p1 = self._port_pos(node, str(e.get("from_port", PORT_OUT)))
            p2 = self._port_pos(other, str(e.get("to_port", PORT_IN)))
        else:
            p1 = self._port_pos(other, str(e.get("from_port", PORT_OUT)))
            p2 = self._port_pos(node, str(e.get("to_port", PORT_IN)))
        dist = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        pad = BULGE_PAD * dist
        return QRectF(p1, p2).normalized().adjusted(-pad, -pad, pad, pad)

    def _group_dirty(self, nids) -> QRectF:
        """一组卡的脏区并集（含相连边鼓包；无卡时退化为空矩形）。"""
        dirty = None
        for nid in nids:
            r = self._dirty_rect_for(nid)
            dirty = r if dirty is None else dirty.united(r)
        return dirty if dirty is not None else QRectF()
