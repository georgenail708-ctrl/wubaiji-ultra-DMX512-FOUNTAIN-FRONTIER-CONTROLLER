# -*- coding: utf-8 -*-
"""Draw 画布（R182）—— 纯 QWidget 自绘（本仓禁 QGraphicsView）。

世界坐标/缩放/平移/点阵网格照 `src/ui/clip_studio/studio_canvas.py` 先例
（同款交互与视觉体系，**独立实现**，不 import 其私有件）：滚轮 x1.25 锚定
光标、中键/右键拖平移、卡片拖动、出桩拖线建边、Del 删除、Home 全览。
规则卡内嵌控件（波形下拉/预览/量/起/落滑杆）以**命中区**实现：点击循环
切换波形，拖动滑杆改值（画布自绘，不嵌真实 QWidget）。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPainterPath
from PyQt6.QtWidgets import QWidget

from . import draw_graph as dc
from . import draw_canvas_edit as dce
from .draw_canvas_geom import rect_from_bounds, seg_bezier
from .draw_canvas_keys import CanvasKeysMixin
from .draw_canvas_paint import DrawPaintMixin, empty_graph
from .draw_canvas_undo import DrawUndoMixin, snap_node

__all__ = ["DrawCanvas", "empty_graph"]

_SCALE_MIN, _SCALE_MAX = 0.1, 6.0
_ZOOM_FACTOR = 1.25
_DRAG_THRESHOLD = 4.0

#: A1：唯一实现在 draw_canvas_geom（xy() 兼容 QPointF/tuple/list；修前下标取值崩溃）
_seg_bezier = seg_bezier

def _in_rect(p: QPointF, rect) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= p.x() <= x1 and y0 <= p.y() <= y1

class DrawCanvas(DrawPaintMixin, DrawUndoMixin, CanvasKeysMixin, QWidget):
    """调制 Draw 画布：卡片 + 连线，纯自绘（禁 QGraphicsView）。

    撤销（R183 4.1）：QUndoStack 命令族在 `draw_canvas_undo.DrawUndoMixin`，
    本类只在各图变更点留薄钩子（push / 快照）。
    """

    graph_changed = pyqtSignal()          # 任何图/参数变化（面板置脏）
    selection_changed = pyqtSignal(str)   # 选中卡 id（""=无）
    card_dropped = pyqtSignal(str, float, float)   # 左栏拖入 (payload, wx, wy)
    undo_redo_state_changed = pyqtSignal(bool, bool)  # (can_undo, can_redo)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_undo()
        self._graph: dict = empty_graph()
        self._view_scale = 1.0
        self._view_offset = QPointF(0.0, 0.0)
        self._sel_ids: set = set()
        self._hover_nid = ""
        self._error_edges: set = set()
        self._painted_nodes = 0
        self._link = None                 # {"src","cur","probe":(nid,port,ok)}
        self._rubber = None
        self._scope_fixture_count = 0     # 轮⑦：scope 真灯数（panel.set_context 注入；0=未知→8 示意）
        self._drag = None                 # {"nid","origin","start","moved"}
        self._slider = None               # {"nid","key","rect"}
        self._panning = False
        self._pan_start = QPointF()
        self._pan_origin = QPointF()
        self._static_cache, self._static_key = None, None
        self._static_offset = QPointF()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)   # B1：修前未开 → Qt 从不派发拖入事件（左栏拖不进）
        self.setMinimumSize(320, 220)

    # ── 坐标与几何（宿主接口：绘制混入依赖）────────────────────────────
    def _scale_band(self) -> float:
        return round(math.log(max(1e-6, self._view_scale)) / math.log(1.25))

    def _px(self, v: float) -> float:
        return float(v)

    def _s2w(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self._view_offset.x()) / self._view_scale,
                       (p.y() - self._view_offset.y()) / self._view_scale)

    def _w2s(self, x: float, y: float) -> QPointF:
        return QPointF(x * self._view_scale + self._view_offset.x(),
                       y * self._view_scale + self._view_offset.y())

    def _card_rect(self, node) -> QRectF:
        return rect_from_bounds(dc.node_rect(node))   # 修前 QRectF(*边界) 把 x1/y1 当宽高

    def _rect_of(self, node) -> QRectF:
        return self._card_rect(node)

    def _out_port(self, node) -> tuple:
        """出桩（右侧中点；源卡起线用）。"""
        x0, y0, x1, y1 = dc.node_rect(node)
        return (x1, (y0 + y1) / 2.0)

    def _in_port(self, node) -> tuple:
        """入桩（左侧中点；规则卡接线用）。"""
        x0, y0, _x1, y1 = dc.node_rect(node)
        return (x0, (y0 + y1) / 2.0)

    def _seg_bezier(self, p1, p2) -> QPainterPath:
        return _seg_bezier(p1, p2)

    # ── 图数据 API（面板消费）───────────────────────────────────────────
    def graph(self) -> dict:
        return self._graph

    def set_graph(self, graph) -> None:
        from .draw_properties_ui import discard_properties
        discard_properties(self)
        src = graph if isinstance(graph, dict) else {}
        self._graph = {"nodes": [dict(n) for n in (src.get("nodes") or [])
                                 if isinstance(n, dict)],
                       "edges": [dict(e) for e in (src.get("edges") or [])
                                 if isinstance(e, dict)]}
        self._sel_ids = set()
        self._undo_reset()   # 文档整体替换不当作可撤销步骤
        self.selection_changed.emit("")
        self.update()

    def clear_graph(self) -> None:
        self.set_graph(empty_graph())
        self.graph_changed.emit()

    def add_source(self, feature: str, x: float, y: float, template: str = "",
                   *, extra=None) -> dict:
        """追加源卡（`extra`：建卡时并入节点的一次性字段，入 undo 快照）。"""
        node = dc.add_source(self._graph["nodes"], feature, x, y, template)
        if extra:
            node.update(extra)
        self._undo_push_add(node)
        self.graph_changed.emit()
        self.update()
        return node

    def add_rule(self, ch_type: str, x: float, y: float) -> dict:
        node = dc.add_rule(self._graph["nodes"], ch_type, x, y)
        self._undo_push_add(node)
        self.graph_changed.emit()
        self.update()
        return node

    def add_effect(self, effect: str, x: float, y: float, *,
                   ch_types=None, params=None) -> dict:
        """追加效果卡（R182 效果库形态；目标通道/参数可预设）。"""
        node = dc.add_effect(self._graph["nodes"], effect, x, y,
                             ch_types=ch_types, params=params)
        self._undo_push_add(node)
        self.graph_changed.emit()
        self.update()
        return node

    def select_only(self, nid: str) -> None:
        self._sel_ids = {str(nid)} if nid else set()
        self.selection_changed.emit(str(nid or ""))
        self.update()

    def zoom_to_fit(self) -> None:
        """全览：按内容包围盒缩放居中（空图回到 1.0/原点）。"""
        nodes = self._graph.get("nodes") or []
        if not nodes:
            self._view_scale, self._view_offset = 1.0, QPointF(0.0, 0.0)
            self.update()
            return
        box = self._card_rect(nodes[0])
        for n in nodes[1:]:
            box = box.united(self._card_rect(n))
        pad = 60.0
        vw, vh = max(60, self.width() - 2 * pad), max(60, self.height() - 2 * pad)
        scale = min(vw / max(1.0, box.width()), vh / max(1.0, box.height()), 2.0)
        self._view_scale = max(_SCALE_MIN, scale)
        cx, cy = box.center().x(), box.center().y()
        self._view_offset = QPointF(self.width() / 2.0 - cx * self._view_scale,
                                    self.height() / 2.0 - cy * self._view_scale)
        self.update()

    def _zoom_at(self, sp: QPointF, factor: float) -> None:
        from .draw_canvas_edit import zoom_at
        z = zoom_at(self._view_scale, factor, sp.x(), sp.y(),   # R183 M0：offset 为 kw-only
                    ox=self._view_offset.x(), oy=self._view_offset.y())
        if z is None:
            return
        self._view_scale, ox, oy = z
        self._view_offset, self._static_cache = QPointF(ox, oy), None   # 底图缓存作废
        self.update()

    # ── 交互：鼠标 ──────────────────────────────────────────────────────
    def _settle_slider(self) -> None:
        """新手势前结算在途滑杆（清查⑤②#5）：push 撤销步再清——修前直接清
        掉含 old 快照的 _slider，左键释放时丢撤销步+脏漏报。"""
        if self._slider is not None:
            self._undo_push_slider()
            self._slider = None
            self.graph_changed.emit()

    def mousePressEvent(self, ev):
        sp, wp = ev.position(), self._s2w(ev.position())
        self._settle_slider()   # 清查⑤②#5：新手势先结算在途滑杆再清
        if self._press_pan(ev, sp):
            ev.accept()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        src = self._hit_port(wp)
        if src is not None:
            self._link = {"src": str(src.get("id", "")), "cur": wp,
                          "probe": ("", "", False)}
            self.update()
            ev.accept()
            return
        if self._press_wave_menu(wp, ev):
            ev.accept()
            return
        if dce.maybe_overflow_menu_at(self, self._graph, wp, ev):
            ev.accept()
            return
        if self._press_slider(wp):
            ev.accept()
            return
        node = dc.hit_node(self._graph, wp.x(), wp.y())
        if node is not None:
            self.select_only(str(node.get("id", "")))
            self._drag = {"nid": str(node.get("id", "")),
                          "origin": (float(node.get("x", 0.0)), float(node.get("y", 0.0))),
                          "start": wp, "moved": False, "old": snap_node(node)}
            ev.accept()
            return
        self._rubber = {"start": wp, "cur": wp}
        ev.accept()

    def _press_pan(self, ev, sp: QPointF) -> bool:
        """中键/右键拖=平移；拖线中右键=取消连线（照专业软件惯例）。"""
        if ev.button() not in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            return False
        if self._link is not None and ev.button() == Qt.MouseButton.RightButton:
            self._link = None
            self.update()
            return True
        self._panning = True
        self._pan_start = sp
        self._pan_origin = QPointF(self._view_offset)
        return True

    def _press_slider(self, wp: QPointF) -> bool:
        """命中效果卡滑杆/目标 chip → 记录拖动/点击起点（命中返回 True）。"""
        node = dc.hit_node(self._graph, wp.x(), wp.y())
        if node is None or str(node.get("kind", "")) != "rule":
            return False
        # 目标 chip 点击 = 勾选/取消该通道类型（点击即生效）
        ct = dc.chip_at(node, wp.x(), wp.y())
        if ct is not None:
            self._toggle_target(node, ct)
            return True
        return self._begin_slider_drag(node, wp)

    def _begin_slider_drag(self, node, wp: QPointF) -> bool:
        """七滑杆命中 → 记录拖动起点（_press_slider 尾段拆出压 CC）。"""
        for key in ("depth", "freq", "attack", "release", "threshold",
                    "smoothing", "spread_deg"):
            rect = dc.control_rect(node, key)
            if rect and _in_rect(wp, rect):
                self._slider = {"nid": str(node.get("id", "")), "key": key,
                                "rect": rect, "old": snap_node(node)}
                self.select_only(str(node.get("id", "")))
                return True
        return False

    def _press_wave_menu(self, wp: QPointF, ev) -> bool:
        """命中规则卡波形下拉 → 弹波形全集菜单（按 ev.globalPosition() 定位）。"""
        node = self._wave_menu_target(wp)
        if node is None:
            return False
        self.select_only(str(node.get("id", "")))
        old = snap_node(node)
        chosen = self._exec_wave_menu(node, ev)
        if chosen is not None:
            node["wave"] = chosen
            # ★ R183 清查④①（P1）：预览/编译读 params["wave"]——修前只写旧
            #   字段，选了不生效（且照样入撤销+置脏，误导）。
            params = node.get("params")
            if isinstance(params, dict):
                params["wave"] = chosen
            self._undo_push_node_state(str(node.get("id", "")), old, "改波形")
            self.graph_changed.emit()
            self.update()
        return True


    def _wave_menu_target(self, wp: QPointF):
        """波形下拉命中判定：返回目标规则卡节点（未命中 → None）。"""
        node = dc.hit_node(self._graph, wp.x(), wp.y())
        if node is None or str(node.get("kind", "")) != "rule":
            return None
        rect = dc.control_rect(node, "wave")
        return node if (rect and _in_rect(wp, rect)) else None

    def _exec_wave_menu(self, node, ev):
        """弹波形菜单；返回选中的波形值（取消 → None）。"""
        from PyQt6.QtWidgets import QMenu
        from .draw_cards import WAVE_CHOICES
        menu = QMenu(self)
        cur = str(node.get("wave", "") or "")
        for label, value in WAVE_CHOICES:
            act = menu.addAction(label)      # 当前项走 Qt 勾选指示（修前 "● " 装饰字符）
            act.setCheckable(True)
            act.setChecked(value == cur)
            act.setData(value)
        chosen = menu.exec(ev.globalPosition().toPoint())
        return None if chosen is None else str(chosen.data() or "")

    def _hit_port(self, wp: QPointF):
        """世界坐标命中源卡出桩（±10 世界单位；只允许源卡起线）。"""
        tol = dc.PORT_HIT_TOL
        for n in reversed(self._graph.get("nodes") or []):
            if str(n.get("kind", "")) != "source":
                continue
            px, py = self._out_port(n)
            if abs(wp.x() - px) <= tol and abs(wp.y() - py) <= tol:
                return n
        return None

    def mouseMoveEvent(self, ev):
        sp, wp = ev.position(), self._s2w(ev.position())
        if self._panning:
            self._view_offset = QPointF(
                self._pan_origin.x() + sp.x() - self._pan_start.x(),
                self._pan_origin.y() + sp.y() - self._pan_start.y())
            self.update()
            return
        if self._link is not None:
            self._link["cur"] = wp
            self._update_probe(wp)
            return
        if self._slider is not None:
            self._drag_slider(wp)
            return
        if self._drag is not None:
            self._drag_card(wp)
            return
        if self._rubber is not None:
            self._rubber["cur"] = wp
            self.update()
            return
        node = dc.hit_node(self._graph, wp.x(), wp.y())
        hover = str(node.get("id", "")) if node is not None else ""
        if hover != self._hover_nid:
            self._hover_nid = hover
            self.update()

    def _update_probe(self, wp: QPointF) -> None:
        """拖线探测：目标规则卡命中=合法；压在其他卡上=非法（红）。"""
        node = dc.hit_link_target(self._graph, wp.x(), wp.y())
        ok = bool(node is not None and str(node.get("kind", "")) == "rule"
                  and str(node.get("id", "")) != self._link["src"])
        self._link["probe"] = (str(node.get("id", "")) if node is not None else "",
                               "in", ok)
        self.update()

    def _drag_card(self, wp: QPointF) -> None:
        d = wp - self._drag["start"]
        if not self._drag["moved"] and math.hypot(d.x(), d.y()) < _DRAG_THRESHOLD:
            return
        node = dc.node_by_id(self._graph, self._drag["nid"])
        if node is not None:
            node["x"] = self._drag["origin"][0] + d.x()
            node["y"] = self._drag["origin"][1] + d.y()
        self._drag["moved"] = True
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._panning:
            self._panning = False
            self.update()
            ev.accept()
            return
        if self._link is not None:
            self._finish_link()
            ev.accept()
            return
        if self._slider is not None:
            self._undo_push_slider()   # press 记的 old 出栈成一条（250ms 合并）
            self._slider = None
            self.graph_changed.emit()
            ev.accept()
            return
        if self._drag is not None:
            self._undo_push_move()     # moved 才入栈（_undo_push_move 自查）
            moved = self._drag["moved"]
            self._drag = None
            if moved:
                self.graph_changed.emit()
            self.update()
            ev.accept()
            return
        if self._rubber is not None:
            self._finish_rubber()
            ev.accept()

    def _finish_link(self) -> None:
        nid, _port, ok = self._link.get("probe", ("", "", False))
        if ok:
            edge = dc.add_edge(self._graph["edges"], self._link["src"], nid)
            self._link = None
            if edge is not None:
                self._undo_push_edge(edge)
                self.graph_changed.emit()
        else:
            self._link = None
        self.update()

    def _finish_rubber(self) -> None:
        """空白区点击=清空选择；拖动框选=选中与框相交的卡片。"""
        band = self._rubber
        self._rubber = None
        r = QRectF(band["start"], band["cur"]).normalized()
        if r.width() < _DRAG_THRESHOLD and r.height() < _DRAG_THRESHOLD:
            if self._sel_ids:
                self._sel_ids = set()
                self.selection_changed.emit("")
            self.update()
            return
        self._sel_ids = {str(n.get("id", "")) for n in self._graph.get("nodes") or []
                         if self._card_rect(n).intersects(r)}
        self.selection_changed.emit(next(iter(self._sel_ids), ""))
        self.update()

    def mouseDoubleClickEvent(self, ev):
        """左双击效果卡：命中控件就地切换（波形/深度/频率循环步进）。"""
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        wp = self._s2w(ev.position())
        node = dc.hit_node(self._graph, wp.x(), wp.y())
        if node is None or str(node.get("kind", "")) != "rule":
            return
        for key in ("wave", "depth", "freq", "attack", "release", "threshold", "smoothing"):
            rect = dc.control_rect(node, key)
            if rect and _in_rect(wp, rect):
                self._cycle_control(node, key)   # DrawUndoMixin：单命令入栈
                ev.accept()
                return

    def wheelEvent(self, ev):
        d = ev.angleDelta().y()
        if d:
            self._zoom_at(ev.position(), _ZOOM_FACTOR if d > 0 else 1.0 / _ZOOM_FACTOR)
            ev.accept()
            return
        super().wheelEvent(ev)

    # ── 左栏拖入（MIME payload → card_dropped 信号；面板负责建卡）────────
    def dragEnterEvent(self, ev):
        from .draw_palette import MIME_CARD
        if ev.mimeData().hasFormat(MIME_CARD):
            ev.acceptProposedAction()
            return
        super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        from .draw_palette import MIME_CARD
        if ev.mimeData().hasFormat(MIME_CARD):
            ev.acceptProposedAction()
            return
        super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        from .draw_palette import MIME_CARD
        if not ev.mimeData().hasFormat(MIME_CARD):
            super().dropEvent(ev)
            return
        payload = bytes(ev.mimeData().data(MIME_CARD)).decode("utf-8", "replace")
        wp = self._s2w(ev.position())
        ev.acceptProposedAction()
        self.card_dropped.emit(str(payload), wp.x(), wp.y())

    def paintEvent(self, _ev):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._painted_nodes = 0
            self._ensure_static(p)
            p.translate(self._view_offset)
            p.scale(self._view_scale, self._view_scale)
            vr = QRectF(self._s2w(QPointF(0, 0)),
                        self._s2w(QPointF(self.width(), self.height()))).normalized()
            self._draw_edges(p, vr)
            self._draw_cards(p, vr)
            if self._rubber is not None:
                self._draw_rubber(p)
            if not (self._graph.get("nodes") or []):
                self._draw_empty_state(p)
            p.resetTransform()
            self._draw_hud(p)
        finally:
            p.end()
