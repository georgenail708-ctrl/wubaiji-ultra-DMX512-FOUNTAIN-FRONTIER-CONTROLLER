# -*- coding: utf-8 -*-
"""Clip Studio 画布核心（R172 设计 §3.4）—— 纯 QWidget + paintEvent（本仓禁 QGraphicsView）。

视图机制**照抄 `src/ui/route_animator/canvas.py`（RouteCanvas）先例**：
`_view_scale/_view_offset`、滚轮 `_zoom_at` x1.25 锚定光标、`_screen_to_world`、
`zoom_to_fit`、静态层 QPixmap 缓存（点阵网格，键含尺寸+scale 档）、`_px` 屏幕恒定、
`_seg_bezier` 三次贝塞尔（R182：标准节点编辑器**水平切线 S 曲线**，节点编辑器
pipe.py 同式；复合桩纵向切线变体）。绘制子过程与交互分拆在
`studio_canvas_paint.CanvasPaintMixin` / `studio_canvas_interact.CanvasInteractMixin`
（≤500 行门禁）。

性能红线（E1）：paintEvent 只画视口相交节点/边（复杂度 ∝ 视口内元素数）；卡片
拖动中只 update 脏区；LOD：scale < 0.6 隐藏美术与摘要。

交互：卡片拖动（最小移动阈值区分点击）/ out 桩拖到 in|composite 桩建边（±阈值
吸附，非法目标红叉+原因提示，Esc·右键·拖回原点取消）/ Del 删除选中（卡+相连边）/
双击卡 → card_edit_requested / 从卡片栏拖入（mime 含卡 key，落点建节点）。
空 sheet 画居中引导（照 RouteCanvas 空态先例）。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget

from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_canvas_insert import EdgeInsertMixin
from src.ui.clip_studio.studio_canvas_interact import CanvasInteractMixin, MIME_CARD
from src.ui.clip_studio.studio_canvas_keys import CanvasKeysMixin
from src.ui.clip_studio.studio_canvas_marks import CanvasMarksMixin
from src.ui.clip_studio.studio_canvas_master import MasterControlMixin
from src.ui.clip_studio.studio_canvas_ops import CanvasOpsMixin
from src.ui.clip_studio.studio_canvas_paint import CanvasPaintMixin, empty_sheet
from src.ui.clip_studio.studio_canvas_static import StaticLayerMixin
from src.ui.clip_studio.studio_canvas_tools import CanvasToolsMixin
from src.ui.clip_studio.studio_cards import PORT_COMPOSITE, PORT_IN, PORT_OUT
from src.ui.clip_studio.studio_collapse import COLLAPSED_CARD_H
from src.ui.clip_studio.studio_frames_ui import FrameUiMixin
from src.ui.clip_studio.studio_graph import edge_index, new_edge, new_node

__all__ = ["ClipStudioCanvas", "MIME_CARD", "CARD_W", "CARD_H", "empty_sheet"]

CARD_W, CARD_H = 176.0, 104.0                    # 卡片世界尺寸（节点 x/y = 卡中心）
_SCALE_MIN, _SCALE_MAX = 0.08, 8.0
GRID_HINT_MIN_W = 1200        # R178 四宫格预显：主画布宽超过此阈值才弹预显虚框


def _near(a: QPointF, b: QPointF, tol: float) -> bool:
    """欧氏距离 ≤ tol（命中测试统一口径）。"""
    dx, dy = a.x() - b.x(), a.y() - b.y()
    return dx * dx + dy * dy <= tol * tol


def _absorb_node_status(status: dict, nid: str, lvl: str) -> None:
    if lvl == "error":
        status[nid] = "error"
    elif lvl in ("warning", "hint") and status.get(nid) != "error":
        status[nid] = "warn"


def _absorb_issue(status: dict, err_edges: set, issue) -> None:
    """单条 Issue → 卡片描边级别（error 压过 warn）与错误边集合。"""
    nid = str(getattr(issue, "node_id", ""))
    if nid:
        _absorb_node_status(status, nid, str(getattr(issue, "level", "")))
    eid = str(getattr(issue, "edge_id", ""))
    if eid and str(getattr(issue, "level", "")) == "error":
        err_edges.add(eid)


class ClipStudioCanvas(CanvasKeysMixin, MasterControlMixin, FrameUiMixin,
                       CanvasInteractMixin, CanvasMarksMixin, EdgeInsertMixin,
                       CanvasOpsMixin, CanvasToolsMixin, StaticLayerMixin,
                       CanvasPaintMixin, QWidget):
    """Clip Studio 卡片画布（world 坐标存节点；数据源 = sheet dict 引用）。"""

    graph_changed = pyqtSignal()                    # 任何结构/参数变更后发一次
    selection_changed = pyqtSignal(str)             # 选中节点 id（清空= ""）
    card_edit_requested = pyqtSignal(str)           # 双击卡 → 参数编辑浮层
    drop_card_requested = pyqtSignal(str, QPointF)  # 卡片栏拖入落点（建卡后）
    grid_preview_activated = pyqtSignal()           # R178：卡片拖入四宫格预显区（宿主切四宫格）
    view_changed = pyqtSignal()                     # 视图变换（缩放/平移）后发一次（小地图/缩放指示）
    notice = pyqtSignal(str)                        # 工具操作的一条中文提示（对齐/布局/搜索…）
    export_requested = pyqtSignal()                 # 「导出图像…」入口（宿主弹保存对话框）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self._sheet: dict = empty_sheet()
        self._view_scale: float = 1.0
        self._view_offset = QPointF(0.0, 0.0)
        self._static_cache = None
        self._static_key = None
        self._static_offset = QPointF(0.0, 0.0)
        self._sel_node = ""                # 当前 active 节点（多选中最后操作的一个）
        self._sel_nodes: set = set()       # R173 Group2：多选节点 id 集合（_sel_node 为其成员或空）
        self._sel_edge = ""
        self._node_status: dict = {}       # nid → "error"|"warn"（Issue 最重级别）
        self._error_edges: set = set()
        self._applied_ids: set = set()     # "已应用"反向编译命中（绿描边）
        self._painted_nodes = 0            # 测试桩：最近一帧实际绘制的节点数
        self._panning = False
        self._pan_start = QPointF(0, 0)
        self._pan_origin = QPointF(0, 0)
        self._space_down = False
        self._drag_nid = ""
        self._drag_origin = QPointF(0, 0)
        self._drag_start = QPointF(0, 0)
        self._drag_moved = False
        self._drag_group: set = set()          # 整组拖动命中的节点 id（单选时为空）
        self._drag_group_origin: dict = {}     # nid → (x0, y0)（整组拖动逐台起点）
        self._rubber_band: dict | None = None  # {"start","cur","mods"}（空白拖拽框选）
        self._link: dict | None = None     # {"src","cur","probe":(nid,port,ok)}
        self._ratio_drag: dict | None = None   # R182 主控过渡卡内嵌比例滑杆拖动
        self._hover_nid = ""
        self._hover_eid = ""
        self._hover_fid = ""               # R183：悬停的 Backdrop 框 id（标题条高亮）
        # R183 深查② #5/#6：Backdrop 分组框 + 卡片对齐吸附（`studio_frames`/`studio_snap`）
        self._sel_frame = ""               # 选中的框 id（删框/右键菜单作用对象）
        self._drag_frame: dict | None = None   # {"mode": "move"|"resize", "frame", 起点…}
        self._snap_guides: list = [[], []]     # 当前帧对齐参考线 [竖线, 横线]（松手清空）
        self._snap_suspended = False       # Alt 按住 = 临时禁用吸附（手势惯例）
        # R178 四宫格自动预显：宿主在单页模式下开启开关；画布宽 ≥ GRID_HINT_MIN_W
        # 才真正弹半透明预显虚框，卡片拖入虚框 → grid_preview_activated（不做按钮）。
        self._grid_hint_enabled = False
        self._grid_hint = False
        # R183 W2-ST：画布工具层状态 + 角落浮层（小地图/缩放指示/搜索框）
        self._init_tools()

    # ── 四宫格预显（R178）─────────────────────────────────────────────
    def set_grid_hint_enabled(self, on: bool) -> None:
        """宿主按视图模式开关预显（单页=开，四宫格=关）。"""
        self._grid_hint_enabled = bool(on)
        self._update_grid_hint()

    def _update_grid_hint(self) -> None:
        val = bool(self._grid_hint_enabled and self.width() >= GRID_HINT_MIN_W)
        if val != self._grid_hint:
            self._grid_hint = val
            self.update()

    def grid_hint_active(self) -> bool:
        return self._grid_hint

    def _grid_hint_rect(self):
        """预显虚框（屏幕坐标）：画布右半区，含 2x2 分隔线由绘制层负责。"""
        w, h = float(self.width()), float(self.height())
        return QRectF(w * 0.44, 10.0, w * 0.56 - 14.0, h - 20.0)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_sheet(self, sheet: dict) -> None:
        """替换数据源（不发光；面板负责刷新状态/问题）。"""
        self._sheet = sheet if isinstance(sheet, dict) else empty_sheet()
        self._sel_node = ""
        self._sel_nodes = set()
        self._sel_edge = ""
        self._sel_frame = ""
        self._drag_frame = None
        self._snap_guides = [[], []]
        self._hover_nid = ""
        self._hover_eid = ""
        self._hover_fid = ""
        self._insert = None
        self._search_hits = []
        self.clear_highlight()
        self._flow_key = None
        self._sync_flow_timer()
        self.update()

    def sheet(self) -> dict:
        return self._sheet

    def set_issues(self, issues) -> None:
        """编译校验结果 → 卡片描边（error=红/warn=琥珀）与错误边（红虚线）。"""
        status: dict = {}
        err_edges: set = set()
        for i in issues or []:
            _absorb_issue(status, err_edges, i)
        self._node_status = status
        self._error_edges = err_edges
        self.update()

    def set_applied_node_ids(self, ids) -> None:
        """「已应用」节点集（反向编译命中，绿描边；同右击菜单高亮语义）。"""
        self._applied_ids = {str(x) for x in (ids or set())}
        self.update()

    def selected_node_id(self) -> str:
        return self._sel_node

    def selected_node_ids(self) -> set:
        """R173 Group2：多选集合（含 active；单选时等价 {selected_node_id()} 或空）。"""
        return set(self._sel_nodes)

    def _select_only(self, nid: str) -> None:
        """单选替换（点击空白卡/普通拖拽起点）。"""
        self._sel_node = nid
        self._sel_nodes = {nid} if nid else set()
        self._sel_edge = ""
        self._sel_frame = ""               # 卡选择与框选择互斥（同 激活项语义）

    def _select_add(self, nid: str) -> None:
        """Shift 追加（已在集合内则只切 active，不移除——照追加选语义）。"""
        if not nid:
            return
        self._sel_nodes.add(nid)
        self._sel_node = nid
        self._sel_edge = ""
        self._sel_frame = ""

    def _select_subtract(self, nid: str) -> None:
        """Ctrl 移除；若移除的是 active，则 active 回退到集合内任一剩余成员。"""
        if not nid:
            return
        self._sel_nodes.discard(nid)
        if self._sel_node == nid:
            self._sel_node = next(iter(self._sel_nodes), "")

    def _clear_selection(self) -> None:
        self._sel_node = ""
        self._sel_nodes = set()
        self._sel_edge = ""
        self._sel_frame = ""

    def select_all(self) -> None:
        """`A`：全选当前 sheet 的所有卡（RouteAnimator/主窗口全局 Ctrl+A 无冲突见先例）。"""
        ids = {str(n.get("id")) for n in self._active_nodes()}
        self._sel_nodes = ids
        self._sel_node = next(iter(ids), "")
        self._sel_edge = ""
        self.selection_changed.emit(self._sel_node)
        self.update()

    def deselect_all(self) -> None:
        """`Alt+A`：清空多选（照通用快捷键惯例）。"""
        self._clear_selection()
        self.selection_changed.emit("")
        self.update()

    def _active_nodes(self) -> list:
        return self._sheet.get("graph", {}).get("nodes", [])

    def _node_by_id(self, nid) -> dict | None:
        return next((n for n in self._active_nodes() if str(n.get("id")) == nid), None)

    def node_spec(self, key):
        """统一规格查询（效果目录 ∪ 便签伪卡；未知 → None）——绘制/命中同源。"""
        return studio_note.lookup_spec(key)

    def node_by_id(self, nid) -> dict | None:
        """按 id 取节点（面板参数浮层/定位用）。"""
        return self._node_by_id(nid)

    # ── 结构编辑（由交互/面板调用）─────────────────────────────────────
    def create_node(self, key: str, world_pos: QPointF) -> dict | None:
        """落点建节点（key 未知 → None；便签伪卡同样可建）。"""
        if self.node_spec(key) is None:
            return None
        node = new_node(str(key), float(world_pos.x()), float(world_pos.y()))
        self._active_nodes().append(node)
        self.update()
        return node

    def _add_edge(self, src: str, dst: str, to_port: str) -> dict | None:
        """建边（自环/空桩/同端重边 → None）。"""
        if src == dst or not to_port:
            return None
        idx = edge_index(self._active_nodes(), self._sheet["graph"].get("edges", []))
        for e in idx["src"].get(src, []):
            if str(e.get("to")) == dst and str(e.get("to_port")) == to_port:
                return None
        edge = new_edge(src, dst, PORT_OUT, to_port)
        self._sheet["graph"].setdefault("edges", []).append(edge)
        return edge

    def _delete_node(self, nid: str) -> None:
        self._delete_nodes({nid})

    def _delete_nodes(self, nids: set) -> None:
        """R173 Group2：批量删（多选 Delete）——一次图变更，调用方只发一次 graph_changed。"""
        if not nids:
            return
        graph = self._sheet["graph"]
        graph["nodes"] = [n for n in graph.get("nodes", []) if str(n.get("id")) not in nids]
        graph["edges"] = [e for e in graph.get("edges", [])
                          if str(e.get("from")) not in nids and str(e.get("to")) not in nids]

    # ── 视图（RouteCanvas 先例照抄）────────────────────────────────────
    def _screen_to_world(self, pos: QPointF) -> QPointF:
        s = max(_SCALE_MIN, float(self._view_scale or 1.0))
        return QPointF((pos.x() - self._view_offset.x()) / s,
                       (pos.y() - self._view_offset.y()) / s)

    _s2w = _screen_to_world

    def _world_to_screen(self, wp: QPointF) -> QPointF:
        s = max(_SCALE_MIN, float(self._view_scale or 1.0))
        return QPointF(self._view_offset.x() + wp.x() * s,
                       self._view_offset.y() + wp.y() * s)

    def _zoom_at(self, sp: QPointF, factor: float) -> None:
        before = self._s2w(sp)
        self._view_scale = max(_SCALE_MIN, min(_SCALE_MAX, self._view_scale * factor))
        self._view_offset = QPointF(sp.x() - before.x() * self._view_scale,
                                    sp.y() - before.y() * self._view_scale)
        self._static_cache = None
        self._sync_zoom_label()          # #13：角落百分比随缩放刷新
        self.view_changed.emit()         # #5：小地图 200ms 节流刷新
        self.update()

    def _px(self, screen_px: float) -> float:
        """屏幕像素 → 世界单位（画在缩放变换里保持屏幕恒定大小）。"""
        return float(screen_px) / max(_SCALE_MIN, float(self._view_scale or 1.0))

    def _vr(self) -> QRectF:
        tl = self._s2w(QPointF(0, 0))
        br = self._s2w(QPointF(float(self.width()), float(self.height())))
        return QRectF(tl, br).normalized()

    def _scale_band(self) -> int:
        return int(round(math.log2(max(1e-4, self._view_scale)) * 8.0))

    def zoom_to_fit(self) -> None:
        """缩放平移到全图可见（照 RouteCanvas，含单轴退化兜底）；折叠隐藏卡不计。"""
        nodes = self._visible_nodes()
        if not nodes:
            nodes = self._active_nodes()
        if not nodes:
            return
        xs = [float(n.get("x", 0.0)) for n in nodes]
        ys = [float(n.get("y", 0.0)) for n in nodes]
        min_x, max_x = min(xs) - CARD_W / 2, max(xs) + CARD_W / 2
        min_y, max_y = min(ys) - CARD_H / 2, max(ys) + CARD_H / 2
        dx = max(max_x - min_x, 200.0)
        dy = max(max_y - min_y, 160.0)
        margin = 60.0
        s = min((self.width() - 2 * margin) / dx, (self.height() - 2 * margin) / dy)
        self._view_scale = max(_SCALE_MIN, min(_SCALE_MAX, s))
        cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
        self._view_offset = QPointF(self.width() / 2.0 - cx * self._view_scale,
                                    self.height() / 2.0 - cy * self._view_scale)
        self._static_cache = None
        self._sync_zoom_label()
        self.view_changed.emit()
        self.update()

    def focus_node(self, node_id: str) -> None:
        """把某卡居中（问题列表「定位」入口）并选中（单选替换，不叠加多选）。"""
        node = self._node_by_id(str(node_id))
        if node is None:
            return
        self._select_only(str(node.get("id")))
        sp = self._world_to_screen(QPointF(float(node.get("x", 0.0)), float(node.get("y", 0.0))))
        d = QPointF(self.width() / 2.0 - sp.x(), self.height() / 2.0 - sp.y())
        self._view_offset = QPointF(self._view_offset.x() + d.x(),
                                    self._view_offset.y() + d.y())
        self.selection_changed.emit(self._sel_node)
        self.view_changed.emit()
        self.update()

    # ── 几何 / 命中 ─────────────────────────────────────────────────────
    @staticmethod
    def _card_rect(node) -> QRectF:
        """卡矩形：常态 CARD_W×CARD_H；折叠/最小化 → 只留标题条高（R183 #14）。

        折叠是纯 UI 层（不写子卡坐标）：连线端口位、命中、脏区、缩略图全部经本
        函数取几何，故折叠后自动重算，无需第二份口径。"""
        cx, cy = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        h = COLLAPSED_CARD_H if node.get("collapsed") else CARD_H
        return QRectF(cx - CARD_W / 2, cy - h / 2, CARD_W, h)

    def _port_pos(self, node, port: str) -> QPointF:
        r = self._card_rect(node)
        if port == PORT_IN:
            return QPointF(r.left(), r.center().y())
        if port == PORT_COMPOSITE:
            return QPointF(r.center().x(), r.bottom())
        return QPointF(r.right(), r.center().y())

    def _port_at(self, wp: QPointF):
        """命中桩位 → (nid, port)；tolerance = _px(9)。折叠/隐藏卡不参与命中。"""
        tol = self._px(9.0)
        hidden = self._hidden_ids()
        for n in reversed(self._active_nodes()):
            if str(n.get("id")) in hidden:
                continue
            spec = self.node_spec(n.get("key"))
            for port in (spec.ports if spec is not None else ()):
                if _near(self._port_pos(n, port), wp, tol):
                    return str(n.get("id")), port
        return "", ""

    def _node_at(self, wp: QPointF) -> dict | None:
        """卡命中（倒序；被折叠隐藏的子卡不参与）。"""
        hidden = self._hidden_ids()
        for n in reversed(self._active_nodes()):
            if str(n.get("id")) in hidden:
                continue
            if self._card_rect(n).contains(wp):
                return n
        return None

    def _nodes_in_rect(self, world_rect: QRectF) -> set:
        """R173 Group2：框选命中——卡矩形与框选矩形相交即算命中（照常见节点编辑器语义）。
        隐藏子卡不参与（折叠后框选不得捞回不可见卡）。"""
        r = world_rect.normalized()
        hidden = self._hidden_ids()
        return {str(n.get("id")) for n in self._active_nodes()
                if str(n.get("id")) not in hidden and self._card_rect(n).intersects(r)}

    # ── Backdrop 分组框（R183 深查② #5）：几何/命中/增删在 `studio_frames_ui`
    #    （FrameUiMixin，含绘制与拖动）—— 本文件贴 500 行门禁，见该模块 docstring。

    def _edge_at(self, wp: QPointF) -> str:
        """命中连线（按当前 edge_style 采样，点距离 < _px(6)）→ edge id。

        路径生成唯一入口 `_edge_path_of`（样式分支 + 折叠隐藏剔除），绘制/预览/
        插入热点同源 —— 防命中与绘制两道几何漂移。"""
        tol = self._px(6.0)
        for e in reversed(self._sheet["graph"].get("edges", [])):
            path = self._edge_path_of(e)
            if path is None:
                continue
            for t in range(1, 13):
                if _near(path.pointAtPercent(t / 12.0), wp, tol):
                    return str(e.get("id", ""))
        return ""

    # ── 绘制编排 ────────────────────────────────────────────────────────
    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        self._ensure_static(p)                    # 静态层（点阵网格底图，含底色）
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._sync_flow_timer()                   # #12：流动动画按需启停（无选中零开销）
        p.save()
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        vr = self._vr()
        self._painted_nodes = 0
        self._draw_frames(p, vr)              # Z 序：框(-2) → 边(-1) → 卡(1)，参考表
        self._draw_edges(p, vr)
        self._draw_nodes(p, vr)
        if self._insert is not None:
            self.draw_insert_preview(p)       # #11：边上插入预览（压在卡上）
        if self._snap_guides:
            self._draw_snap_guides(p)         # 对齐参考线压在卡上（可见性优先）
        if self._rubber_band is not None:
            self._draw_rubber_band(p)
        p.restore()
        if not self._active_nodes():
            self._draw_empty_state(p)     # 空态引导（照 RouteCanvas 空态先例）
        if self._grid_hint:
            self._draw_grid_preview(p)
        self._draw_hud(p)
        p.end()
