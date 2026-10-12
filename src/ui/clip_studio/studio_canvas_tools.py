# -*- coding: utf-8 -*-
"""画布工具层 · 基础块（R183 W2-ST #5/#10/#13/#15 + 键位/上下文菜单接线）。

两文件拆分的理由：本批新增 12 项功能，单文件必破 500 行门禁。分工：

* **本文件（`studio_canvas_tools`）**：状态与角落浮层（小地图/缩放指示）、撤销
  钩子（一次画布操作=一条撤销的 token 机制）、可见性判据（折叠隐藏）、键位分发
  表、缩放/导航/导出、Ctrl+右键上下文菜单。
* **`studio_canvas_ops`（`CanvasOpsMixin`）**：具体编辑操作 —— 连线样式、对齐/
  分布、自动布局、折叠/最小化、禁用、便签、搜索、流动动画、边上插入。

宿主（ClipStudioCanvas）提供 `_sheet/_sel_nodes/_sel_node/_sel_edge/_active_nodes/
_node_by_id/_add_edge_to(...)` 等（鸭子类型）；signals 由宿主类定义。

键位（画布聚焦时经 `ShortcutOverride` 抢占，未聚焦不干扰全局快捷键）：
Ctrl+F 搜索 / Ctrl+L·R·T·B 对齐 / Ctrl+D 均分 / Ctrl+G 收束子图 / M 禁用切换 /
+·- 缩放 / 0 = 100% / Ctrl+右键 = 上下文菜单（普通右键仍是平移，R172 契约）。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtWidgets import QMenu

from src.ui.clip_studio import studio_collapse
from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_minimap import (MINI_H, MINI_MARGIN, MINI_W, MinimapWidget,
                                               ZoomLabel)
from src.ui.clip_studio.studio_search import SearchBoxWidget

log = logging.getLogger(__name__)

__all__ = ["CanvasToolsMixin", "FLOW_INTERVAL_MS", "ZOOM_STEP", "TOOL_CLAIM_KEYS", "CTRL_ACTIONS"]

#: 流动动画定时器间隔（毫秒；任务书：≥30fps 节流）。
FLOW_INTERVAL_MS = 33
#: ± 键缩放档（与滚轮同档）。
ZOOM_STEP = 1.25
#: 小地图显示下限（低于此尺寸 = 四宫格单元格，160×100 缩略会盖住内容）。
MINIMAP_MIN_W = 420
MINIMAP_MIN_H = 280

_EPS = 1e-9
_ZOOM_IN = 1.25                       # ± 键与滚轮同档（interact `_ZOOM_FACTOR`）

#: Ctrl+键位 → (方法名, 参数)；键位表集中在此，keyPressEvent 不含长 if 链（CC 门禁）。
_CTRL_ACTIONS = {
    Qt.Key.Key_F: ("open_search", None),
    Qt.Key.Key_D: ("distribute_selection", None),
    Qt.Key.Key_L: ("align_selection", "left"),
    Qt.Key.Key_R: ("align_selection", "right"),
    Qt.Key.Key_T: ("align_selection", "top"),
    Qt.Key.Key_B: ("align_selection", "bottom"),
    Qt.Key.Key_G: ("collapse_selection_into_active", None),
}
#: 无修饰工具键（ShortcutOverride 判据用）。
TOOL_CLAIM_KEYS = (Qt.Key.Key_Equal, Qt.Key.Key_Plus, Qt.Key.Key_Minus,
                   Qt.Key.Key_0, Qt.Key.Key_M)
#: 旧名兼容（模块内部引用）。
_TOOLS_PLAIN_KEYS = TOOL_CLAIM_KEYS


class _CardMenuHandler:
    """卡片右键菜单的动作表与分发（拆出混入方法压嵌套门禁）。"""

    def __init__(self, canvas, node) -> None:
        self._cv = canvas
        self._node = node
        self._nid = str(node.get("id", ""))

    def build(self, menu) -> dict:
        """建菜单 → ``{QAction: 处理函数}``（便签项仅便签卡出现）。"""
        acts = {}
        if studio_note.is_note(self._node):
            acts[menu.addAction("编辑便签…")] = self._edit_note
        acts[menu.addAction("收束子图（选中卡到此卡）")] = lambda: \
            self._cv.collapse_selection_into_active()
        acts[menu.addAction("展开")] = lambda: self._cv.expand_selected()
        acts[menu.addAction("启用" if self._node.get("disabled") else "禁用（bypass）")] = \
            self._toggle_disabled
        acts[menu.addAction("删除卡片")] = self._delete
        return acts

    def dispatch(self, chosen, acts: dict) -> None:
        """菜单选择 → 处理函数（取消/未收录 → 什么都不做）。"""
        fn = acts.get(chosen)
        if fn is not None:
            fn()

    def _edit_note(self) -> None:
        self._cv._edit_note_dialog(self._nid)

    def _toggle_disabled(self) -> None:
        self._cv.set_nodes_disabled([self._nid], not self._node.get("disabled"))

    def _delete(self) -> None:
        self._cv._begin_undo_op()
        self._cv._delete_nodes({self._nid})
        self._cv._commit_graph_change()
        self._cv.update()


class CanvasToolsMixin:
    """画布工具基础块：钩子/浮层/键位/导航（宿主 = ClipStudioCanvas）。"""

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._flow_key = None
        self._sync_flow_timer()

    def hideEvent(self, event):  # noqa: N802
        self._flow_timer.stop()
        self._flow_key = None
        super().hideEvent(event)

    # ── 状态与角落浮层初始化（宿主 __init__ 调用）───────────────────────
    def _init_tools(self) -> None:
        """工具层状态 + 角落浮层（小地图/缩放指示）构建。"""
        self._undo_token = False
        self._drag_undo_done = False
        self._flush_fn = None
        self._snapshot_fn = None
        self._insert: dict | None = None
        self._hl_nid = ""
        self._hl_until = 0.0
        self._last_notice = ""
        self._flow_running = False
        self._flow_phase = 0.0
        self._flow_ids: set = set()
        self._flow_key = None
        self._flow_timer = QTimer(self)
        self._flow_timer.setInterval(FLOW_INTERVAL_MS)   # ≈30fps（任务书节流下限）
        self._flow_timer.timeout.connect(self._tick_flow)
        self._hl_timer = QTimer(self)
        self._hl_timer.setSingleShot(True)
        self._hl_timer.timeout.connect(self.clear_highlight)
        self._search: SearchBoxWidget | None = None
        self._search_hits: list = []
        self._minimap = MinimapWidget(self, parent=self)
        self._zoom_label = ZoomLabel(self)
        self._zoom_label.clicked.connect(self.reset_zoom)
        self.view_changed.connect(self._sync_zoom_label)
        self._sync_zoom_label()
        self._reposition_overlays()

    def _sync_zoom_label(self) -> None:
        self._zoom_label.set_scale(float(self._view_scale or 1.0))

    def _reposition_overlays(self) -> None:
        """角落浮层摆位：小地图右下、缩放指示其上、搜索框顶部居中。

        画布过小（四宫格单元格）→ 小地图自动隐藏（160×100 会盖住整格内容）；
        缩放指示仍保留（它只有 52×18）。
        """
        w, h = self.width(), self.height()
        self._minimap.setVisible(bool(w >= MINIMAP_MIN_W and h >= MINIMAP_MIN_H))
        x = max(0, w - MINI_W - MINI_MARGIN)
        self._minimap.move(x, max(0, h - MINI_H - MINI_MARGIN))
        self._zoom_label.move(x, max(0, h - MINI_H - 2 * MINI_MARGIN))
        if self._search is not None:
            bw = max(180, min(260, w - 24))
            self._search.setFixedWidth(bw)
            self._search.move(max(0, (w - bw) // 2), 8)

    def minimap(self):
        """角落小地图控件（测试/宿主入口）。"""
        return self._minimap

    def zoom_label(self):
        """角落缩放指示控件（测试/宿主入口）。"""
        return self._zoom_label

    # ── 撤销（#15）：一次画布操作 = 一条撤销 ───────────────────────────
    def set_undo_hooks(self, flush_fn=None, snapshot_fn=None) -> None:
        """注入面板侧钩子（flush 挂起编辑 / 压撤销快照）；两者皆可缺省。"""
        self._flush_fn = flush_fn
        self._snapshot_fn = snapshot_fn

    def _begin_undo_op(self) -> bool:
        """操作前压一条撤销（先 flush 挂起编辑，内容 = 操作前状态）。

        快照源是**工程里的** studio 状态（`persist` 在操作提交后才写），故此刻读到的
        仍是操作前状态；顺序与面板 `_on_graph_changed` 一致（flush → snapshot）。
        面板在同一轮 graph_changed 里 `consume_undo_token()` 跳过自己的快照
        （`no_inner_snapshots` 同思路），保证一次操作只压一条。
        """
        for fn in (self._flush_fn, self._snapshot_fn):
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                log.warning("[studio_canvas_tools] 撤销钩子失败", exc_info=True)
        self._undo_token = True
        return True

    def _ensure_drag_undo(self) -> None:
        """拖动首帧真移动前压一条撤销（#15：拖动一次=一条；重复调用只压一次）。"""
        if getattr(self, "_drag_undo_done", False):
            return
        self._drag_undo_done = True
        self._begin_undo_op()

    def consume_undo_token(self) -> bool:
        """面板取走「本操作的快照已压」标记（一次性；取走即清）。"""
        tok = bool(self._undo_token)
        self._undo_token = False
        return tok

    def _commit_graph_change(self) -> None:
        """统一提交：发 graph_changed（面板据此消费 token）+ 清 token 防泄漏。"""
        self.graph_changed.emit()
        self._undo_token = False
        # R193/P2-07（UI-08）：图变更失效流动 memo——旧键只数节点/边数量，
        #   重连边（数量不变）不触发重算，流动虚线画在旧边集上。
        self._flow_key = None

    def _notify(self, text) -> None:
        """给用户的一条短提示（notice 信号 + 缓存最近一条，测试可断言）。"""
        self._last_notice = str(text)
        self.notice.emit(str(text))

    def last_notice(self) -> str:
        """最近一条工具提示（测试入口）。"""
        return str(self._last_notice)

    # ── 可见性（折叠子卡）───────────────────────────────────────────────
    def _hidden_ids(self) -> set:
        """当前被收起隐藏的子卡 id 集（折叠纯 UI 层，编译器只读）。"""
        return studio_collapse.hidden_ids(self._active_nodes())

    def _visible_nodes(self) -> list:
        """可见节点（几何/命中/绘制/布局统一口径；无折叠 → 原表）。"""
        hidden = self._hidden_ids()
        if not hidden:
            return self._active_nodes()
        return [n for n in self._active_nodes() if str(n.get("id")) not in hidden]

    def _visible_sel_ids(self) -> list:
        """选中集 ∩ 可见卡（按节点表原序 —— 对齐/布局确定性输出）。"""
        hidden = self._hidden_ids()
        sel = set(self._sel_nodes) or ({self._sel_node} if self._sel_node else set())
        return [str(n.get("id")) for n in self._active_nodes()
                if str(n.get("id")) in sel and str(n.get("id")) not in hidden]

    # ── #13 缩放（± 键 / 100% 回位 / 小地图导航）───────────────────────
    def zoom_step(self, factor: float) -> None:
        """以视口中心为锚缩放（± 键；与滚轮同档 1.25）。"""
        self._zoom_at(QPointF(self.width() / 2.0, self.height() / 2.0), float(factor))

    def reset_zoom(self) -> None:
        """缩放回 100%（角落指示点击 / `0` 键），视口中心世界点不动。"""
        scale = max(1e-6, float(self._view_scale or 1.0))
        if abs(scale - 1.0) < _EPS:
            self._notify("已是 100%")
            return
        self.zoom_step(1.0 / scale)
        self._notify("缩放已回到 100%")

    def zoom_percent_text(self) -> str:
        """当前缩放百分比文本（角落指示同源）。"""
        return f"{float(self._view_scale or 1.0):.0%}"

    def navigate_to_world(self, wp: QPointF) -> None:
        """把某世界点居中（小地图点击/拖动导航）。"""
        s = max(1e-6, float(self._view_scale or 1.0))
        self._view_offset = QPointF(self.width() / 2.0 - wp.x() * s,
                                    self.height() / 2.0 - wp.y() * s)
        self._static_cache = None
        self.view_changed.emit()
        self.update()

    # ── #10 导出画布 PNG ───────────────────────────────────────────────
    def export_png(self, path) -> bool:
        """整帧导出 PNG（`QWidget.grab()` 含 HUD/小地图）；成功 → True。"""
        pm = self.grab()
        return bool(pm is not None and not pm.isNull() and pm.save(str(path), "PNG"))

    # ── 上下文菜单（Ctrl+右键；普通右键仍是平移，R172 契约）────────────
    def _press_tools_context(self, ev, sp, wp) -> bool:
        """Ctrl+右键 → 卡片/空白上下文菜单；其余按键组合 → False（交回旧通道）。"""
        if ev.button() != Qt.MouseButton.RightButton or not (
                ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return False
        if self._link is not None:
            return False                    # 拖线中 Ctrl+右键仍走取消
        node = self._node_at(wp)
        if node is not None:
            self._card_menu(node, sp)
        else:
            self._canvas_menu(sp)
        return True

    def _card_menu(self, node, sp) -> None:
        """卡菜单：便签编辑 / 收束子图 / 展开 / 禁用 / 删除。"""
        menu = QMenu(self)
        handler = _CardMenuHandler(self, node)
        acts = handler.build(menu)
        handler.dispatch(menu.exec(self.mapToGlobal(sp.toPoint())), acts)   # B3：sp 为局部坐标

    def _edit_note_dialog(self, nid) -> None:
        """便签编辑弹窗（多行纯文本；确定才提交）。"""
        from src.ui.clip_studio.studio_dialogs import NoteTextDialog
        dlg = NoteTextDialog(self.note_text_of(nid), self)
        if dlg.exec():
            self.set_note_text(nid, dlg.text())

    def _canvas_menu(self, sp) -> None:
        """空白菜单：新建便签 / 自动布局 / 导出图像（动作表直拷，无嵌套分支）。"""
        menu = QMenu(self)
        acts = {menu.addAction("新建便签"): self._menu_add_note,
                menu.addAction("自动布局"): lambda: self.auto_layout_cards(),
                menu.addAction("导出图像…"): lambda: self.export_requested.emit()}
        fn = acts.get(menu.exec(self.mapToGlobal(sp.toPoint())))   # B3：sp 为局部坐标
        if fn is not None:
            fn()

    def _menu_add_note(self) -> None:
        """空白菜单「新建便签」：落在视口中心（世界坐标）。"""
        self.add_note(self._s2w(QPointF(self.width() / 2.0, self.height() / 2.0)))

    # ── 键位（画布聚焦；ShortcutOverride 判据 + 分发）─────────────────
    def _claimed_key(self, ev) -> bool:
        """ShortcutOverride 判据：Ctrl+工具键 / 无修饰工具键 / 旧 `_CLAIM_KEYS`。"""
        key, mods = ev.key(), ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if ctrl and not shift and not alt and key in _CTRL_ACTIONS:
            return True
        if not (ctrl or shift or alt) and key in _TOOLS_PLAIN_KEYS:
            return True
        return key in self._CLAIM_KEYS

    def _try_tools_keys(self, key, mods) -> bool:
        """工具键分发（命中返回 True）；Ctrl 组合走键位表（CC 门禁）。"""
        if mods & Qt.KeyboardModifier.ControlModifier:
            return self._try_ctrl_key(key)
        if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            self.zoom_step(_ZOOM_IN)
            return True
        if key == Qt.Key.Key_Minus:
            self.zoom_step(1.0 / _ZOOM_IN)
            return True
        if key == Qt.Key.Key_0:
            self.reset_zoom()
            return True
        if key == Qt.Key.Key_M:
            self.toggle_disabled_selection()
            return True
        return False

    def _try_ctrl_key(self, key) -> bool:
        """Ctrl+字母键位表分发（不在表内 → False 交回旧通道）。"""
        spec = _CTRL_ACTIONS.get(key)
        if spec is None:
            return False
        name, arg = spec
        fn = getattr(self, name)
        fn() if arg is None else fn(arg)
        return True

    def _toolbar_edge_style_label(self) -> str:
        """工具栏提示文案：当前样式 + 切换方向（chrome 用）。"""
        from src.ui.clip_studio import studio_edge_style as _es
        label = dict(_es.EDGE_STYLE_LABELS).get(self.edge_style(), "")
        return f"连线样式：{label}（点击切换 曲线/直角/直线）"
