# -*- coding: utf-8 -*-
"""Clip Studio 画布交互混入（R172 §3.4；R173 Group2 多选；R182 内嵌控件；R183 吸附/分组框）。

鼠标（平移/卡片拖动/桩位拖线/选择/框选/分组框拖动/双击/边中点插入）、
滚轮缩放、拖入建卡；键位在 `studio_canvas_keys.CanvasKeysMixin`。主控过渡卡内嵌控件在
`studio_canvas_master`、对齐吸附在 `studio_frames_ui`、脏区计算在 `studio_canvas_dirty`
（本文件贴 500 行门禁）。宿主属性/方法以鸭子类型访问（`_sheet/_sel_nodes/_drag_nid/
_drag_group/_rubber_band/_ratio_drag/_sel_frame` 与 `_s2w/_px/_port_at/_node_at/
_edge_at/_node_by_id/_card_rect/_select_*` 等）；signals 由宿主类定义。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt

from src.ui.clip_studio.studio_canvas_dirty import DirtyRegionMixin, _BULGE_PAD  # noqa: F401
from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_cards import PORT_COMPOSITE, PORT_IN, PORT_OUT

__all__ = ["CanvasInteractMixin", "MIME_CARD"]

MIME_CARD = "application/x-qascade-studio-card"   # 卡片栏 QDrag mime（值=卡 key）
_DRAG_THRESHOLD_PX = 4.0                          # 卡片/框选最小移动阈值（区分点击）
_ZOOM_FACTOR = 1.25
_DIRTY_PAD_PX = 10                                # 拖动脏区外扩（须覆盖 composite 桩环半径 8.2px）


class CanvasInteractMixin(DirtyRegionMixin):
    """鼠标/键盘/拖放交互（宿主 paintEvent 编排不含本混入）。"""

    # ── 鼠标 ────────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):  # noqa: N802
        sp, wp = ev.position(), self._s2w(ev.position())
        if self._press_tools_context(ev, sp, wp):     # R183：Ctrl+右键 = 上下文菜单
            ev.accept()
            return
        if self._press_context_or_pan(ev, sp, wp):
            ev.accept()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self._press_link_or_card(wp, ev):
            return
        eid, mid = self._edge_hotspot_at(wp)          # R183 #11：边中点热点拖出插入
        if eid:
            self._begin_insert_drag(eid, wp, mid)
            ev.accept()
            return
        eid = self._edge_at(wp)
        if eid:
            self._clear_selection()
            self._sel_edge = eid
            self.selection_changed.emit("")
            self.update()
            ev.accept()
            return
        if self._press_frame(wp, ev):
            ev.accept()
            return
        # 空白区：点击 vs 拖拽框选留到 release 判定（通用框选先例）
        self._rubber_band = {"start": wp, "cur": wp, "mods": ev.modifiers()}
        ev.accept()

    def _press_context_or_pan(self, ev, sp: QPointF, wp: QPointF) -> bool:
        """右键先判分组框菜单（节点编辑器同款：框右键 = 改名/改色/删除），未命中落回
        取消/平移通道（拖线中右键仍是取消连线，优先级最高）。**卡层优先**（Z 序
        node > backdrop）：右键落在卡上即使卡在框内也不弹框菜单——否则框会吞掉卡片
        右键语义（误删/误改整组）。"""
        if (ev.button() == Qt.MouseButton.RightButton and self._link is None
                and self._node_at(wp) is None and self._frame_menu(wp, sp)):
            return True
        return self._press_cancel_or_pan(ev, sp)

    def _press_cancel_or_pan(self, ev, sp: QPointF) -> bool:
        """按下先裁决取消/平移：拖线中右键 = 取消连线（照节点编辑器惯例）；中键/右键/
        空格+左键 = 平移。命中返回 True（调用方 accept）。"""
        if self._link is not None and ev.button() == Qt.MouseButton.RightButton:
            self._cancel_link()
            return True
        want_pan = ev.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or (
            ev.button() == Qt.MouseButton.LeftButton and self._space_down)
        if not want_pan:
            return False
        self._panning = True
        self._pan_start = sp
        self._pan_origin = QPointF(self._view_offset)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def _press_link_or_card(self, wp: QPointF, ev) -> bool:
        """左键按下命中：out 桩 → 开始拖线；主控过渡卡内嵌控件 → 就地操作；
        卡片 → 选中并准备拖动（含多选）。"""
        nid, port = self._port_at(wp)
        if nid and port == PORT_OUT:
            self._link = {"src": nid, "cur": wp, "probe": ("", "", False)}
            self._update_link_probe(wp)
            ev.accept()
            return True
        if self._press_master_control(wp):
            ev.accept()
            return True
        node = self._node_at(wp)
        if node is not None:
            self._press_on_node(node, wp, ev.modifiers())
            ev.accept()
            return True
        return False

    def _press_on_node(self, node, wp: QPointF, mods) -> None:
        """R173 Group2：Shift 追加 / Ctrl 移除 / 普通点击（已在多选内保留整组，
        否则替换为单选）；随后按当前选择集准备单卡或整组拖动。"""
        nid = str(node.get("id"))
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self._select_add(nid)
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self._select_subtract(nid)
            self.selection_changed.emit(self._sel_node)
            self.update()
            return
        elif not (nid in self._sel_nodes and len(self._sel_nodes) > 1):
            self._select_only(nid)
        else:
            self._sel_node = nid
        self._begin_node_drag(nid, node, wp)
        self.selection_changed.emit(self._sel_node)
        self.update()

    def _begin_node_drag(self, nid: str, node, wp: QPointF) -> None:
        self._drag_nid = nid
        self._drag_origin = QPointF(float(node.get("x", 0.0)), float(node.get("y", 0.0)))
        self._drag_start = wp
        self._drag_moved = False
        self._drag_undo_done = False      # R183 #15：首次真移动压一条撤销（拖动一次=一条）
        if len(self._sel_nodes) > 1 and nid in self._sel_nodes:
            self._drag_group = set(self._sel_nodes)
            self._drag_group_origin = {n: (float(m.get("x", 0.0)), float(m.get("y", 0.0)))
                                       for n in self._drag_group
                                       if (m := self._node_by_id(n)) is not None}
        else:
            self._drag_group = set()
            self._drag_group_origin = {}

    def _update_link_probe(self, wp: QPointF) -> None:
        """拖线探测：(目标节点, 桩, 是否兼容)；桩未中但压在卡体 → 非法目标红叉。"""
        nid, port = self._port_at(wp)
        ok = bool(nid and nid != self._link["src"] and port in (PORT_IN, PORT_COMPOSITE))
        if not nid:
            body = self._node_at(wp)
            if body is not None and str(body.get("id")) != self._link["src"]:
                nid = str(body.get("id"))
        self._link["probe"] = (nid, port, ok)
        self.update()

    def mouseMoveEvent(self, ev):  # noqa: N802
        sp, wp = ev.position(), self._s2w(ev.position())
        self._sync_snap_modifier(ev.modifiers())
        if self._panning:
            self._view_offset = QPointF(self._pan_origin.x() + sp.x() - self._pan_start.x(),
                                        self._pan_origin.y() + sp.y() - self._pan_start.y())
            self.view_changed.emit()          # 小地图/缩放指示：平移也需刷新（静态层自行位移复用）
            self.update()
            return
        if self._ratio_drag is not None:
            self._drag_ratio_to(wp)
            return
        if self._link is not None:
            self._link["cur"] = wp
            self._update_link_probe(wp)
            return
        if self._insert is not None:          # R183 #11：边中点拖出插入
            self._drag_insert(wp)
            return
        if self._drag_frame is not None:
            self._drag_frame_to(wp)
            return
        if self._drag_nid:
            self._drag_card(wp)
            return
        if self._rubber_band is not None:
            self._rubber_band["cur"] = wp
            self.update()
            return
        self._update_hover(wp)

    def _sync_snap_modifier(self, mods) -> None:
        """Alt 按住 = 临时禁用吸附（松开恢复；通用手势惯例）。

        拖动中切换修饰键时清一次参考线（禁用瞬间旧线会留在屏上）。"""
        want = bool(mods & Qt.KeyboardModifier.AltModifier)
        if want != self._snap_suspended:
            self._snap_suspended = want
            if want:
                self.clear_snap_guides()

    def _update_hover(self, wp: QPointF) -> None:
        """悬停态刷新：卡/边/框命中 → 高亮 + 光标（独立函数压 mouseMoveEvent 的 CC）。"""
        node = self._node_at(wp)
        hover = str(node.get("id")) if node else ""
        hover_edge = "" if hover else self._edge_at(wp)
        hover_fid = "" if (hover or hover_edge) else self._frame_hit_id(wp)
        if (hover != self._hover_nid or hover_edge != self._hover_eid
                or hover_fid != self._hover_fid):
            self._hover_nid = hover
            self._hover_eid = hover_edge
            self._hover_fid = hover_fid
            self.update()
        cursor = (Qt.CursorShape.PointingHandCursor if self._port_at(wp)[0]
                  else Qt.CursorShape.ArrowCursor)
        if self.cursor().shape() != cursor:
            self.setCursor(cursor)

    def _drag_card(self, wp: QPointF) -> None:
        """卡片拖动（世界坐标）；多选整组一起移，单选原逻辑不变。

        R183 深查② #6：位移先过对齐吸附（`_snap_drag_delta`），参考线由该 helper 写进
        画布状态；本函数只负责写坐标 + 脏区重绘（门禁：CC ≤ 8）。"""
        if self._drag_group:
            self._drag_group_card(wp)
            return
        node = self._node_by_id(self._drag_nid)
        if node is None:
            self._drag_nid = ""
            return
        d = wp - self._drag_start
        if not self._drag_moved and math.hypot(d.x(), d.y()) < self._px(_DRAG_THRESHOLD_PX):
            return
        origins = {self._drag_nid: (self._drag_origin.x(), self._drag_origin.y())}
        d = self._snap_drag_delta(d, origins)
        self._write_card_pos(node, QPointF(self._drag_origin.x() + d.x(),
                                           self._drag_origin.y() + d.y()),
                             self._drag_nid)

    def _write_card_pos(self, node, pos: QPointF, nid: str) -> None:
        """单卡落位 + 脏区重绘（吸附前后并集，防旧位置残影）。"""
        self._ensure_drag_undo()          # R183 #15：首帧真移动前压撤销（内容=拖动前）
        dirty = self._dirty_rect_for(nid)
        node["x"], node["y"] = pos.x(), pos.y()
        dirty = dirty.united(self._dirty_rect_for(nid))
        self._drag_moved = True
        self.update(dirty.adjusted(-_DIRTY_PAD_PX, -_DIRTY_PAD_PX,
                                   _DIRTY_PAD_PX, _DIRTY_PAD_PX))

    def _drag_group_card(self, wp: QPointF) -> None:
        """R173 Group2：整组拖动——各卡按自身起点+同一位移量整体平移，脏区取并集。

        R183：位移先过对齐吸附（候选 = 整个选中组的外包围盒，节点编辑器语义）。"""
        d = wp - self._drag_start
        if not self._drag_moved and math.hypot(d.x(), d.y()) < self._px(_DRAG_THRESHOLD_PX):
            return
        self._ensure_drag_undo()          # R183 #15：整组拖动同样一条撤销
        d = self._snap_drag_delta(d, dict(self._drag_group_origin))
        dirty = self._group_dirty(self._drag_group)
        for nid, (x0, y0) in self._drag_group_origin.items():
            node = self._node_by_id(nid)
            if node is not None:
                node["x"] = x0 + d.x()
                node["y"] = y0 + d.y()
        dirty = dirty.united(self._group_dirty(self._drag_group))
        self._drag_moved = True
        self.update(dirty.adjusted(-_DIRTY_PAD_PX, -_DIRTY_PAD_PX,
                                   _DIRTY_PAD_PX, _DIRTY_PAD_PX))

    #: 松手分派表（顺序即优先级；各段拆函数压 mouseReleaseEvent 的 CC）。
    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            ev.accept()
            return
        if self._release_active_mode():
            ev.accept()
            return
        if self._rubber_band is not None:
            self._end_rubber_band()
            ev.accept()

    def _release_active_mode(self) -> bool:
        """松手分派：滑杆/连线/插入/框拖/卡拖（命中返回 True）。"""
        if self._ratio_drag is not None:
            changed = bool(self._ratio_drag.get("changed"))
            self._ratio_drag = None
            if changed:
                self.graph_changed.emit()   # 松手提交一次（拖动中已即时改卡参数）
            return True
        if self._link is not None:
            self._finish_link()
            return True
        if self._insert is not None:          # R183 #11：边中点拖出松手
            self._finish_insert()
            return True
        if self._drag_frame is not None:
            self._end_frame_drag()
            return True
        if self._drag_nid:
            self._end_node_drag()
            return True
        return False

    def _end_node_drag(self) -> None:
        """松手：真移动过才发一次 graph_changed；位置入签名（graph_signature 含 x/y）。

        R183 #15：拖动一次 = 一条撤销 —— `_begin_undo_op` 在**移动前**已由
        `_drag_card/_drag_group_card` 首帧调用（内容 = 拖动前状态），此处只提交。"""
        moved = self._drag_moved
        self._drag_nid = ""
        self._drag_moved = False
        self._drag_group = set()
        self._drag_group_origin = {}
        self.clear_snap_guides()        # 松手撤参考线（拖动中有效）
        if moved:
            self._commit_graph_change()  # 位置入签名（graph_signature 含 x/y）
        else:
            self._undo_token = False     # 未真移动：清掉可能残留的撤销标记
        self.update()

    def _end_rubber_band(self) -> None:
        """框选松手：低于阈值 = 点击空白（无修饰键才清空选择）；否则按矩形选中，
        Shift 并入现选、Ctrl 从现选扣除、无修饰键整体替换。"""
        band = self._rubber_band
        self._rubber_band = None
        d = band["cur"] - band["start"]
        mods = band["mods"]
        if math.hypot(d.x(), d.y()) < self._px(_DRAG_THRESHOLD_PX):
            if not (mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)):
                self._clear_selection()
                self.selection_changed.emit("")
                self.update()
            return
        hit = self._nodes_in_rect(QRectF(band["start"], band["cur"]))
        self._apply_rubber_band_selection(hit, mods)

    def _apply_rubber_band_selection(self, hit: set, mods) -> None:
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self._sel_nodes |= hit
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self._sel_nodes -= hit
        else:
            self._sel_nodes = set(hit)
        if self._sel_node not in self._sel_nodes:
            self._sel_node = next(iter(self._sel_nodes), "")
        self.selection_changed.emit(self._sel_node)
        self.update()

    def _cancel_link(self) -> None:
        """取消拖线（Esc / 右键 / 松手在非法目标同口径）：清预览不留半成品。"""
        self._link = None
        self.update()

    def _finish_link(self) -> None:
        """松手：兼容桩（±阈值吸附）→ 建边 + graph_changed；非法/拖回原点 → 取消。"""
        nid, port, ok = self._link.get("probe", ("", "", False))
        if ok:
            self._add_edge(self._link["src"], nid, port)
            self._commit_graph_change()
            self._link = None
            self.update()
        else:
            self._cancel_link()

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        """双击：#3 便签 → 文本编辑；#14 标题条 → 折叠/最小化；卡体 → 参数浮层。"""
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(ev)
            return
        wp = self._s2w(ev.position())
        node = self._node_at(wp)
        if node is None:
            super().mouseDoubleClickEvent(ev)
            return
        if not studio_note.is_note(node) and self._in_card_header(node, wp):
            self.toggle_card_collapse(str(node.get("id")))   # #14 标题条双击
            ev.accept()
            return
        self.card_edit_requested.emit(str(node.get("id")))
        ev.accept()

    def _in_card_header(self, node, wp: QPointF) -> bool:
        """点是否落在卡标题条内（双击折叠与双击编辑的分界；便签除外）。"""
        r = self._card_rect(node)
        return bool(r.top() <= wp.y() <= r.top() + self._header_h(r))

    def wheelEvent(self, ev):  # noqa: N802
        d = ev.angleDelta().y()
        if d:
            self._zoom_at(ev.position(), _ZOOM_FACTOR if d > 0 else 1.0 / _ZOOM_FACTOR)
            ev.accept()
            return
        super().wheelEvent(ev)

    # ── 拖入（卡片栏 → 画布）────────────────────────────────────────────
    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasFormat(MIME_CARD):
            ev.acceptProposedAction()
            return
        super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasFormat(MIME_CARD):
            ev.acceptProposedAction()
            return
        super().dragMoveEvent(ev)

    def dropEvent(self, ev):  # noqa: N802
        if not ev.mimeData().hasFormat(MIME_CARD):
            super().dropEvent(ev)
            return
        key = bytes(ev.mimeData().data(MIME_CARD)).decode("utf-8", "replace")
        wp = self._s2w(ev.position())
        # R178 四宫格预显：卡片拖入预显虚框 → 不建卡，发信号让宿主切四宫格视图
        if self._grid_hint and self._grid_hint_rect().contains(ev.position()):
            ev.acceptProposedAction()
            self.grid_preview_activated.emit()
            return
        # R183 W2-ST：注释便签拖入（伪卡；无桩不可接线，走同一 create_node 通道）
        if str(key) == studio_note.NOTE_KEY:
            ev.acceptProposedAction()
            self.add_note(wp)
            return
        node = self.create_node(key, wp)
        ev.acceptProposedAction()
        if node is not None:
            self.drop_card_requested.emit(str(key), wp)
            self.graph_changed.emit()

    def resizeEvent(self, ev):  # noqa: N802
        self._static_cache = None
        self._update_grid_hint()
        self._reposition_overlays()          # R183：角落浮层随尺寸重摆
        super().resizeEvent(ev)
