# -*- coding: utf-8 -*-
"""Clip Studio 面板壳（R172 设计 §3.1 / B10-B12）—— palette + 画布 + sheet 标签条 + 应用回路。

布局（R182）：QSplitter 横向 [palette | 右侧竖直：工具行 + (单页画布 | 四宫格) + 问题列表]，
**顶部** sheet 标签条（QTabBar + 新建 + 右键，见 `studio_sheet_bar.SheetBarMixin`）。

应用回路（假接线防线，W1d2 接线契约）：注入 apply_fields_fn/snapshot_fn/list_clips_fn/
bind_clip_fn/project_getter。graph_changed（闭包携带 sheet 身份）→ **编辑前** snapshot_fn()
压一条联合域撤销（R173：原在 300ms 后应用时才快照 → 图已改、快照不含编辑前的图）→
300ms 单发 QTimer → 对 pending sheet compile_sheet：有 error 只刷新问题列表不应用；否则经
`studio_session.ApplyLedger` 按 sheet 下发**差量**（挪卡=零应用，删卡=复位默认值）。
切标签/四宫格聚焦/增删页前立即 flush 挂起编辑；**换工程/撤销重灌则丢弃**挂起编辑
（此时 clip 已换或已回滚，flush 会写错对象；撤销前由 main_window 先 flush_pending）。
失败键（如平滑）→ 状态标签警告，账本不记该键、下次编辑自动重试。开关灯=全局会话级，
由账本派生（`_sync_light`），不逐 sheet 下发。

持久化（R97 守卫铁律）：load_state 记 `self._hydrated_for = project`；persist 只在 is 当前工程时回写。
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import QEvent, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui import qascade_theme as _th
from src.ui.clip_studio import studio_master
from src.ui.clip_studio import studio_note
from src.ui.clip_studio.studio_apply import merge_overrides
from src.ui.clip_studio.studio_canvas import CARD_H, CARD_W, ClipStudioCanvas, empty_sheet
from src.ui.clip_studio.studio_cards import CARD_CATALOG
from src.ui.clip_studio.studio_compile import compile_sheet, recommendations
from src.ui.clip_studio.studio_dialogs import CardParamDialog
from src.ui.clip_studio.studio_graph import new_state, normalize_state
from src.ui.clip_studio.studio_inspector import StudioInspector, refresh_inspector
from src.ui.clip_studio.studio_palette import CardPalette
from src.ui.clip_studio.studio_panel_tools import PanelToolsMixin
from src.ui.clip_studio.studio_session import ApplyLedger, release_light_cards, sync_light_suppressed
from src.ui.clip_studio.studio_status import show_compile_status, show_pending
from src.ui.clip_studio.studio_sheet_bar import SheetBarMixin
from src.ui.clip_studio.studio_chrome import ChromeMixin
from src.ui.main_icon_library import load_tabler_icon

__all__ = ["ClipStudioPanel", "persist_clip_studio_state"]

_GRID_SLOTS = 4
_QUICK_NUDGE = 44.0     # 快捷创建落点避让步长（约半张卡，沿对角错开）
_QUICK_TRIES = 16       # 最多避让次数（超出则落在最后一点，不再找）

def _rect_free(cv, pos: QPointF) -> bool:
    """快捷创建落点是否不与现有卡重叠（同尺寸卡：|Δ中心| 均小于卡尺寸即相交）。"""
    for n in cv.sheet().get("graph", {}).get("nodes", []):
        if (abs(float(n.get("x", 0.0)) - pos.x()) < CARD_W
                and abs(float(n.get("y", 0.0)) - pos.y()) < CARD_H):
            return False
    return True


def _free_spot(cv, pos: QPointF) -> QPointF:
    """快捷创建落点避让：中心被占则沿对角错开（成熟软件不叠卡，成熟软件先例）。"""
    spot = QPointF(pos)
    for _ in range(_QUICK_TRIES):
        if _rect_free(cv, spot):
            return spot
        spot = QPointF(spot.x() + _QUICK_NUDGE, spot.y() + _QUICK_NUDGE)
    return spot


def persist_clip_studio_state(panel, project) -> None:
    """R97 守卫：只有当前 hydrate 的工程才允许回写（防换工程后串写）。"""
    if getattr(panel, "_hydrated_for", None) is not project:
        return
    try:
        project.clip_studio_state = panel.save_state()
    except Exception:
        pass


class ClipStudioPanel(PanelToolsMixin, ChromeMixin, SheetBarMixin, QWidget):
    """Clip 附加处理 Studio 面板（W1d1 UI 层）；标签条 UI 在 `SheetBarMixin`（≤500 行拆分）。"""

    state_edited = pyqtSignal()             # 供 W1d2 观察面板侧编辑（persist/撤销链可挂）

    APPLY_DEBOUNCE_MS = 300

    def __init__(self, apply_fields_fn=None, snapshot_fn=None, list_clips_fn=None, *,
                 bind_clip_fn=None, project_getter=None, optimize_fn=None, parent=None):
        super().__init__(parent)
        self._apply_fields_fn = apply_fields_fn
        self._snapshot_fn = snapshot_fn
        self._list_clips_fn = list_clips_fn
        self._bind_clip_fn = bind_clip_fn
        self._project_getter = project_getter
        self._optimize_fn = optimize_fn
        self._hydrated_for = None
        self._state = new_state()
        self._ledger = ApplyLedger()       # 按 sheet 的已应用 fields（R173 取代全局单槽 prev）
        self._apply_failures = {}          # UI 会话事实按页隔离；刷新不抹去失败
        self._pending_sheet_id = ""        # 防抖挂起编辑所属的 sheet 身份（P1 竞态修复）
        self._last_added_key = ""          # 拖入/快捷创建的卡（推荐上下文）
        self._issue_items: list = []       # R178：当前编译问题（原底部消息列表的一行化数据）
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(self.APPLY_DEBOUNCE_MS)
        self._apply_timer.timeout.connect(self._flush_pending_apply)
        self._build_ui()
        self._refresh_all()

    # ── UI 构建 ─────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        # R182：sheet 标签条移到**面板顶部**（splitter 之前）——标签是页面级导航，
        # 压在底部时与画布 HUD/问题列表抢视线（用户点名）。
        v.addWidget(self._build_sheet_bar())
        self._canvas = ClipStudioCanvas()
        self._grid_canvases = [ClipStudioCanvas() for _ in range(_GRID_SLOTS)]
        self._wire_canvas(self._canvas, is_main=True)
        for cv in self._grid_canvases:
            self._wire_canvas(cv, is_main=False)
            cv.installEventFilter(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._palette = CardPalette()
        self._palette.quick_create_requested.connect(self._quick_create)
        self._palette.card_drag_started.connect(self._on_drag_started)
        splitter.addWidget(self._palette)
        splitter.addWidget(self._build_right_side())
        self._inspector = StudioInspector(self)
        splitter.addWidget(self._inspector)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([248, 696, 280])
        v.addWidget(splitter, 1)
        self._install_panel_tools()               # R183 W2-ST：撤销钩子/缩略图条/导出入口

    def _wire_canvas(self, cv: ClipStudioCanvas, is_main: bool) -> None:
        # graph_changed 携带 sheet 身份（emit 时刻解析，防 300ms 防抖窗口内切标签/
        # 切四宫格/换工程后 flush 错表应用或丢应用）：单页画布=active；四宫格画布=其装载 sheet
        if is_main:
            cv.graph_changed.connect(lambda _cv=cv: self._on_graph_changed(
                str(self._state.get("active_sheet", "")), _cv))
        else:
            cv.graph_changed.connect(lambda _cv=cv: self._on_graph_changed(
                str(cv.sheet().get("id") or ""), _cv))
        cv.card_edit_requested.connect(self._on_card_edit)
        cv.drop_card_requested.connect(self._on_card_dropped)
        if is_main:
            cv.selection_changed.connect(self._on_selection)
            cv.grid_preview_activated.connect(self._on_grid_preview_activated)

    def _on_grid_preview_activated(self) -> None:
        """R178：卡片拖入四宫格预显虚框 → 直接切四宫格（不做按钮的启用路径）。"""
        if self._state.get("view_mode") != "grid":
            self._state["view_mode"] = "grid"
            self._refresh_view_mode()
            self._persist()
            self.state_edited.emit()

    # ── sheet 状态访问 ──────────────────────────────────────────────────
    def _sheets(self) -> list:
        return self._state.setdefault("sheets", [])

    def _active_sheet(self) -> dict | None:
        aid = str(self._state.get("active_sheet", ""))
        return next((s for s in self._sheets() if str(s.get("id")) == aid), None)

    def _sheet_by_id(self, sid: str) -> dict | None:
        return next((s for s in self._sheets() if str(s.get("id")) == sid), None)

    def canvas(self) -> ClipStudioCanvas:
        """主画布（W1d2 接线入口之一）。"""
        return self._canvas

    def _groups_hint(self, sheet_id: str) -> int:
        """R178：绑定 clip 的 fade_groups 最大值作为 V8 展开提示（反向页的模块头
        group_batch 不落 fade_groups 字段——不传 hint 则真实 clip 进页即 V8）。"""
        best = 0
        for c in self._bound_clips(sheet_id):
            try:
                best = max(best, int(getattr(c, "fade_groups", 0) or 0))
            except (TypeError, ValueError):
                continue
        return best

    def _clips(self) -> list:
        try:
            return list(self._list_clips_fn() or []) if self._list_clips_fn else []
        except Exception:
            return []

    def _bound_clips(self, sheet_id: str) -> list:
        return [c for c in self._clips()
                if str(getattr(c, "process_sheet_id", "") or "") == str(sheet_id)]

    # ── 刷新（tab/视图模式/编译状态/推荐）───────────────────────────────
    def _refresh_all(self) -> None:
        self._sync_tabs()
        self._refresh_view_mode()
        self._refresh_compile_status()

    def _sync_tabs(self) -> None:
        self._tabs.blockSignals(True)
        while self._tabs.count() > len(self._sheets()):
            self._tabs.removeTab(self._tabs.count() - 1)
        while self._tabs.count() < len(self._sheets()):
            self._tabs.addTab("")
        for i, s in enumerate(self._sheets()):
            n = len(self._bound_clips(str(s.get("id"))))
            self._tabs.setTabText(i, f"{s.get('name', '')} ({n})" if n else str(s.get("name", "")))
        aid = str(self._state.get("active_sheet", ""))
        idx = next((i for i, s in enumerate(self._sheets()) if str(s.get("id")) == aid), 0)
        self._tabs.setCurrentIndex(idx)
        self._tabs.blockSignals(False)
        sheet = self._active_sheet()
        self._sheet_label.setText(sheet.get("name", "") if sheet else "（无后处理页）")
        self._bind_label.setText(f"绑定 Clip: {len(self._bound_clips(aid))}")
        self._sync_sheet_thumbs()          # R183 #16：缩略图条随标签同步

    def _refresh_view_mode(self) -> None:
        grid = self._state.get("view_mode") == "grid"
        self._btn_grid.setChecked(grid)
        self._btn_single.setChecked(not grid)
        self._stack.setCurrentIndex(1 if grid else 0)
        # R178 四宫格自动预显：单页 + 宽画布才弹预显虚框（画布 resize 时自行复核阈值）
        self._canvas.set_grid_hint_enabled(not grid)
        if grid:
            self._refresh_grid()

    def _refresh_grid(self) -> None:        # 四宫格（B12）：前 4 个 sheet，active 优先
        aid = str(self._state.get("active_sheet", ""))
        ordered = sorted(self._sheets(), key=lambda s: str(s.get("id")) != aid)[:_GRID_SLOTS]
        for i, cv in enumerate(self._grid_canvases):
            sheet = ordered[i] if i < len(ordered) else None
            cv.set_sheet(sheet if sheet is not None else empty_sheet())
            cv.set_issues(compile_sheet(
                sheet, self._groups_hint(str(sheet.get("id", "")))).issues
                if sheet is not None else [])

    def _on_view_mode_clicked(self) -> None:
        mode = "grid" if self._btn_grid.isChecked() else "single"
        if mode == self._state.get("view_mode"):
            return
        self._state["view_mode"] = mode
        self._refresh_view_mode()
        self._persist()
        self.state_edited.emit()

    def _show_help(self) -> None:
        """显示 Studio 面板帮助对话框（300ms 防抖机制说明）。"""
        from src.ui.help_dialogs import show_help_dialog, STUDIO_HELP_HTML
        show_help_dialog(self, "Clip Studio 帮助", STUDIO_HELP_HTML)

    # ── 编译 / 状态条（R178：问题列表一行化）/ 推荐 ─────────────────────
    def issues(self) -> list:
        """当前 active sheet 的编译问题（Issue 列表；原 _issues 消息区的数据面 API）。"""
        return list(self._issue_items)

    def _refresh_compile_status(self, res=None, reset_canvas: bool = True) -> None:
        sheet = self._active_sheet()
        if reset_canvas:
            self._canvas.set_sheet(sheet if sheet is not None else empty_sheet())
        if res is None:
            res = (compile_sheet(sheet, self._groups_hint(str(sheet.get("id", ""))))
                   if sheet is not None else None)
        issues = res.issues if res is not None else []
        self._issue_items = list(issues)
        self._canvas.set_issues(issues)
        show_compile_status(self, sheet, res)
        recs = recommendations(sheet, self._last_added_key)[:4] if sheet is not None else []
        self._palette.set_recommendations(recs)
        refresh_inspector(self)

    # ── 应用回路（防抖 300ms，账本差量下发）─────────────────────────────
    def _on_graph_changed(self, sheet_id: str = "", cv=None) -> None:
        """图变更入口（`_wire_canvas` 闭包携带 sheet 身份）：先落上一笔挂起编辑，再压
        **一条**编辑前撤销快照（工程里的 studio 状态此刻仍是编辑前 —— `_persist` 在后），
        记 pending sheet id，300ms 窗口内切换后 flush 仍应用到**编辑时**的 sheet。

        R183 W2-ST #15：画布侧工具操作已自压一条快照（undo token 非空）时跳过本处
        快照 —— 一次操作仍是一条撤销（`no_inner_snapshots` 同思路）。"""
        self._flush_pending_before_context_switch()
        token = bool(getattr(cv, "consume_undo_token", lambda: False)())
        if not token:
            self._snapshot()
        self._pending_sheet_id = str(sheet_id or "")
        self._persist()
        from .studio_thumb_refresh import refresh_committed_thumbnail
        refresh_committed_thumbnail(self, sheet_id)
        self.state_edited.emit()
        self._apply_timer.start(self.APPLY_DEBOUNCE_MS)
        show_pending(self, str(sheet_id or self._state.get("active_sheet", "")))

    def _snapshot(self) -> None:
        if self._snapshot_fn is not None:
            self._snapshot_fn()

    def _flush_pending_apply(self) -> None:
        """防抖到点：对 pending sheet（缺省回退 active）编译并按账本差量应用。"""
        self._apply_timer.stop()
        sheet = self._sheet_by_id(self._pending_sheet_id) or self._active_sheet()
        self._pending_sheet_id = ""
        if sheet is None:
            return
        is_active = str(sheet.get("id")) == str(self._state.get("active_sheet", ""))
        res = compile_sheet(sheet, self._groups_hint(str(sheet.get("id", ""))))
        if is_active:
            # 仅 active sheet 刷主画布问题列表/状态标签（非 active 的挂起 flush 只落盘编辑）
            self._refresh_compile_status(res=res, reset_canvas=False)
        if res.errors:
            return                      # 有 error：只刷新问题列表，不应用
        self._apply_compiled(sheet, res, is_active)

    def _apply_compiled(self, sheet, res, is_active) -> None:
        """无 error → 账本对绑定 clips 下发差量（无绑定只记账）；失败键不入账、下次
        编辑自动重试（其它键已应用结果不受影响；状态标签转警告仅 active 语境）。"""
        sid = str(sheet.get("id"))
        ids = [str(getattr(c, "id", "")) for c in self._bound_clips(sid)]
        failed = self._ledger.apply(sid, ids, merge_overrides(res), self._apply_fields_fn)
        self._sync_light()
        self._warn_failed(failed, sid, res=res)

    def _warn_failed(self, failed, sheet_id: str = "", *, res=None) -> None:
        sid = str(sheet_id or self._state.get("active_sheet", ""))
        self._apply_failures[sid] = tuple(str(key) for key in failed)
        if sid == str(self._state.get("active_sheet", "")):
            self._refresh_compile_status(res=res, reset_canvas=False)

    def _sync_light(self) -> None:
        """全局开关灯 = 账本派生（任一有绑定 clip 的 sheet 已应用禁灯）。"""
        bound = {str(getattr(c, "process_sheet_id", "") or "") for c in self._clips()}
        sync_light_suppressed(self._ledger.light_wanted(bound.__contains__), self._hydrated_for)

    def _flush_pending_before_context_switch(self) -> None:
        """sheet 上下文切换（切标签/四宫格聚焦/增删页/绑定）前：挂起编辑
        立即 flush 到**旧 pending sheet**（切换不吞编辑）。"""
        inspector = getattr(self, "_inspector", None)
        if inspector is not None:
            inspector.flush()
        if self._apply_timer.isActive():
            self._flush_pending_apply()

    def flush_pending(self) -> None:
        """公开入口：撤销/重做前由 main_window 调用（先落挂起编辑再回滚，防丢/错写）。"""
        self._flush_pending_before_context_switch()

    def force_apply_now(self) -> None:      # 跳过防抖立即执行（测试 / W1d2 手动入口）
        self._flush_pending_apply()

    def _on_card_dropped(self, key: str, _world_pos) -> None:
        self._last_added_key = str(key)
        self._prefill_master_rows()
        self._refresh_compile_status(reset_canvas=False)

    def _prefill_master_rows(self) -> None:
        """R182：主控过渡卡拖入/快捷创建后按绑定 clip 的通道类型预填行表。"""
        if studio_master.prefill_from_panel(self, str(self._state.get("active_sheet", ""))):
            self._persist()

    def _on_drag_started(self, key: str) -> None:
        self._last_added_key = str(key)

    def _on_selection(self, node_id: str) -> None:
        self._inspector.flush()
        refresh_inspector(self, node_id)

    def _quick_create(self, key: str) -> None:
        # 清查⑧B P2-1：无 sheet 时（新工程空态）画布的 _sheet 是游离空表——
        #   直接建卡只落进画布、_persist 写出 {"sheets": []} → 保存重开丢卡。
        #   与拖入建卡的语义对齐：先自动建一页再落卡。
        if self._active_sheet() is None:
            self.add_sheet()
        cv = self._canvas
        center = cv._s2w(QPointF(cv.width() / 2.0, cv.height() / 2.0))
        node = cv.create_node(str(key), _free_spot(cv, center))
        if node is not None:
            self._last_added_key = str(key)
            self._prefill_master_rows()
            self._on_graph_changed(str(self._state.get("active_sheet", "")), cv)
            self._refresh_compile_status(reset_canvas=False)

    def _on_card_edit(self, node_id: str) -> None:
        """双击卡 → 参数编辑浮层 → 写节点 params → graph_changed。

        R182：主控过渡卡走专属弹窗（行表增删 + 淡入/淡出曲线分设）；其余卡走通用表单。
        R183 W2-ST：注释便签走多行文本弹窗（#3，伪卡不在效果目录里，单独分派）。
        """
        self._flush_pending_before_context_switch()
        node = self._canvas.node_by_id(str(node_id))
        if node is None:
            return
        if studio_note.is_note(node):
            self.edit_note(str(node_id))
            return
        spec = CARD_CATALOG.get(str(node.get("key", "")))
        if spec is None:
            return
        if spec.key == "trans_opt":
            from src.ui.clip_studio.studio_optimizer import run_optimizer_action
            run_optimizer_action(self, node)
            return
        values = self._edit_values(node, spec)
        if values is not None:
            node["params"] = values
            self._on_graph_changed(str(self._state.get("active_sheet", "")), self._canvas)
            self._refresh_compile_status(reset_canvas=False)

    def _edit_values(self, node, spec) -> dict | None:
        """弹窗往返：返回新 params；取消 → None。

        R182：主控过渡卡走专属弹窗（`studio_master_dialog`：行表增删 + 淡入/淡出
        曲线分设）；其余卡走通用表单。
        """
        if studio_master.is_master_card(node):
            from src.ui.clip_studio.studio_master_dialog import MasterTransitionDialog
            dlg = MasterTransitionDialog(dict(node.get("params") or {}), self)
        else:
            dlg = CardParamDialog(spec, dict(node.get("params") or {}), self)
        return dlg.values() if dlg.exec() else None

    def _locate_issue(self, node_id: str) -> None:
        """按节点 id 定位卡片（R178 签名收窄：原收 QListWidgetItem）。"""
        nid = str(node_id or "")
        if nid:
            self._canvas.focus_node(nid)

    # ── sheet 标签切换（新建/重命名/删除/绑定在 SheetBarMixin）────────────
    def _on_tab_changed(self, index: int) -> None:
        self._flush_pending_before_context_switch()   # 切页前落盘旧页挂起编辑
        if 0 <= index < len(self._sheets()):
            self._state["active_sheet"] = str(self._sheets()[index].get("id"))
            self._refresh_all()
            self._persist()
            self.state_edited.emit()

    # ── 四宫格点击切换（B12）───────────────────────────────────────────
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.MouseButtonPress and obj in self._grid_canvases:
            self._flush_pending_before_context_switch()   # 聚焦切换前落盘挂起编辑
            i = self._grid_canvases.index(obj)
            aid = str(self._state.get("active_sheet", ""))
            sheets = sorted(self._sheets(), key=lambda s: str(s.get("id")) != aid)
            if i < len(sheets):
                self._state["active_sheet"] = str(sheets[i].get("id"))
                self._state["view_mode"] = "single"
                self._refresh_all()
                self._persist()
                self.state_edited.emit()
            return True
        return super().eventFilter(obj, ev)

    # ── 持久化 ─────────────────────────────────────────────────────────
    def mark_applied_nodes(self, ids) -> None:
        """「已应用」绿描边集（W1d2 应用成功后调用；画布语义同菜单绿亮）。"""
        self._canvas.set_applied_node_ids(ids)

    def _persist(self) -> None:
        # persist_clip_studio_state 自带 R97 守卫与异常吞噬
        if self._project_getter is not None:
            persist_clip_studio_state(self, self._project_getter())

    def save_state(self) -> dict:
        """schema 照 studio_graph.new_state（sheets/active_sheet/view_mode）。"""
        return copy.deepcopy(self._state)

    def load_state(self, state, project, *, session_reset: bool = True) -> None:
        """normalize 后灌入并换绑 R97 hydrate 锚；**丢弃**挂起编辑（R173：此刻 clip 已换
        工程或已被撤销回滚，flush 会把旧编辑写进新对象 —— 撤销前的 flush 由
        main_window 负责）。账本以灌入图为已应用基线；`session_reset`（换工程/首装）
        把开关灯卡释放为"开"（R110 临时状态不跨工程），撤销/重做传 False 保留。"""
        self._inspector.discard()
        self._apply_timer.stop()
        self._pending_sheet_id = ""
        self._hydrated_for = project
        self._state = normalize_state(state)
        if session_reset and release_light_cards(self._state, project):
            self._persist()
        self._ledger.reset(self._sheets())
        self._apply_failures.clear()
        self._sync_light()
        self._refresh_all()
