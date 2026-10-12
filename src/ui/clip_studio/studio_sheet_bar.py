# -*- coding: utf-8 -*-
"""Clip Studio sheet 标签条 + sheet 生命周期混入（R172 设计 §3.1）—— 从 studio_panel 拆出
（≤500 行门禁；R173 把新建/重命名/删除/绑定/open_for_clip 一并迁入）。

与宿主（ClipStudioPanel）以鸭子类型协作，依赖宿主属性/方法：`_tabs/_state/_canvas/
_sheets()/_sheet_by_id()/_clips()/_bound_clips()/_bind_clip_fn/_apply_fields_fn/_ledger/
_snapshot()/_sync_light()/_warn_failed()/_refresh_all()/_sync_tabs()/_persist()/
_flush_pending_before_context_switch()/state_edited`。应用回路/防抖 flush 语义仍在
studio_panel.py。R173：每个改工程的 sheet 动作（新建/重命名/删除/绑定/新建反向页）
先压**一条**联合域撤销快照；绑定即应用（新 clip 按该页已应用值全量下发）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QInputDialog, QMenu, QMessageBox, QTabBar, QToolButton, QWidget

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_canvas import CARD_H, CARD_W
from src.ui.clip_studio.studio_compile import reverse_compile
from src.ui.clip_studio.studio_dialogs import BindClipsDialog
from src.ui.clip_studio.studio_graph import new_edge, new_node, new_sheet
from src.ui.clip_studio.studio_reverse import wire_reverse_edges
from src.ui.main_icon_library import load_tabler_icon

__all__ = ["SheetBarMixin"]


class SheetBarMixin:
    """sheet 标签条构建 + 右键菜单 + sheet 生命周期（宿主 = ClipStudioPanel）。"""

    def _build_sheet_bar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tabs.customContextMenuRequested.connect(self._on_tab_menu)
        h.addWidget(self._tabs, 1)
        # R183 W2-ST #16：多 sheet 缩略图条（80px/枚；复用总览管线；点击切页）
        from src.ui.clip_studio.studio_sheet_thumbs import SheetThumbStrip
        self._sheet_thumbs = SheetThumbStrip(bar)
        self._sheet_thumbs.setVisible(False)      # 有页时才显示（`_sync_sheet_thumbs`）
        h.addWidget(self._sheet_thumbs)
        add = QToolButton()
        add.setIcon(load_tabler_icon("circle-plus", 15, _th.TEXT_PRIMARY))
        add.setToolTip("新建后处理页")
        add.setAutoRaise(True)
        add.setFixedSize(24, 24)
        add.clicked.connect(self.add_sheet)
        h.addWidget(add)
        return bar

    def _on_tab_menu(self, pos) -> None:
        index = self._tabs.tabAt(pos)
        if not (0 <= index < len(self._sheets())):
            return
        sheet = self._sheets()[index]
        menu = QMenu(self)
        act_rename = menu.addAction("重命名")
        act_bind = menu.addAction("绑定 Clip…")
        act_del = menu.addAction("删除")
        chosen = menu.exec(self._tabs.mapToGlobal(pos))
        if chosen is act_rename:
            name, ok = QInputDialog.getText(self, "重命名后处理页", "名称：",
                                            text=str(sheet.get("name", "")))
            if ok:
                self.rename_sheet(str(sheet.get("id")), name)
        elif chosen is act_bind:
            self._open_bind_dialog(sheet)
        elif chosen is act_del:
            self.delete_sheet(str(sheet.get("id")))

    def _open_bind_dialog(self, sheet) -> None:
        dlg = BindClipsDialog(str(sheet.get("id")), str(sheet.get("name", "")),
                              self._clips(), self._bind_clip_through, parent=self)
        dlg.exec()
        self._sync_tabs()

    def _bind_clip_through(self, clip_id: str, sheet_id) -> None:
        """绑定回调（一次勾选 = 一条撤销）。R173：绑定即按该页**已应用** fields 全量
        下发到新 clip（原只写 process_sheet_id → 卡片显示与 clip 实值不符）；解绑保留现值。"""
        self._flush_pending_before_context_switch()   # 该页挂起编辑先落账本
        self._snapshot()
        if self._bind_clip_fn is not None:
            self._bind_clip_fn(clip_id, sheet_id)
        if sheet_id:
            self._warn_failed(self._ledger.apply_full(
                str(sheet_id), [str(clip_id)], self._apply_fields_fn), str(sheet_id))
        self._sync_light()
        self._sync_tabs()
        self._refresh_compile_status(reset_canvas=False)

    # ── sheet 生命周期（新建/重命名/删除/open_for_clip）─────────────────
    def add_sheet(self) -> dict:
        """新建 sheet 并置为 active（"＋"按钮 / 右键菜单入口）。"""
        self._flush_pending_before_context_switch()   # 切 active 前落盘挂起编辑
        self._snapshot()
        sheet = new_sheet([s.get("name") for s in self._sheets()])
        self._sheets().append(sheet)
        self._state["active_sheet"] = str(sheet["id"])
        self._refresh_all()
        self._persist()
        self.state_edited.emit()
        return sheet

    def rename_sheet(self, sid: str, name: str) -> None:
        sheet = self._sheet_by_id(sid)
        if sheet is None:
            return
        self._snapshot()
        sheet["name"] = str(name).strip() or str(sheet.get("name", ""))
        self._sync_tabs()
        self._persist()
        self.state_edited.emit()

    def delete_sheet(self, sid: str, *, confirm_fn=None) -> bool:
        """删除 sheet（有绑定 clip → 确认；删除仅解绑，clip 字段保留现值）。"""
        self._flush_pending_before_context_switch()   # 被删页的挂起编辑先落其绑定 clip
        sheet = self._sheet_by_id(sid)
        if sheet is None:
            return False
        bound = self._bound_clips(sid)
        if bound:
            ask = confirm_fn or (lambda: QMessageBox.question(
                self, "删除后处理页",
                f"「{sheet.get('name')}」仍有 {len(bound)} 个 Clip 绑定，确认删除？（仅解绑）")
                == QMessageBox.StandardButton.Yes)
            if not ask():
                return False
        self._snapshot()
        if self._bind_clip_fn is not None:
            for c in bound:
                self._bind_clip_fn(str(getattr(c, "id", "")), None)
        self._sheets()[:] = [s for s in self._sheets() if str(s.get("id")) != sid]
        self._ledger.forget(sid)
        self._apply_failures.pop(sid, None)
        if str(self._state.get("active_sheet")) == sid:
            self._state["active_sheet"] = str(self._sheets()[0]["id"]) if self._sheets() else ""
        self._sync_light()                             # 删禁灯页 → 灯输出随派生恢复
        self._refresh_all()
        self._persist()
        self.state_edited.emit()
        return True

    def open_for_clip(self, clip) -> dict | None:
        """入口：有绑定 → 选中该 sheet；无 → 新建 + reverse_compile 反向卡 + 绑定。

        反向卡 = clip 现值 → 账本只记基线**不下发**（反向编译不保证逐字段还原，
        下发反而会改 clip）；此后该页编辑按差量下发。"""
        self._flush_pending_before_context_switch()   # 切 active 前落盘挂起编辑
        existing = self._sheet_by_id(str(getattr(clip, "process_sheet_id", "") or ""))
        if existing is not None:
            self._state["active_sheet"] = str(existing["id"])
            self._refresh_all()
            return existing
        self._snapshot()
        sheet = new_sheet([s.get("name") for s in self._sheets()])
        applied = self._place_reverse_nodes(sheet, clip)
        self._sheets().append(sheet)
        self._ledger.record(sheet)
        self._state["active_sheet"] = str(sheet["id"])
        if self._bind_clip_fn is not None and getattr(clip, "id", None):
            self._bind_clip_fn(str(clip.id), str(sheet["id"]))
        # R178：先绑定后刷新——状态条编译（V8 组数提示按绑定 clip 的 fade_groups
        # 展开）需要绑定关系先就位，否则真实 clip 进页即 V8 假报错。
        self._refresh_all()
        self._canvas.set_applied_node_ids(applied)
        self._sync_tabs()
        self._persist()
        self.state_edited.emit()
        return sheet

    def _place_reverse_nodes(self, sheet, clip) -> list:
        """反向编译 → 网格错位布局建节点 + R178 模块自动串联边；返回「已应用」节点 id 集。

        补边（`wire_reverse_edges`）：指定组卡+方向卡在 clip 现值里本就是一个复合
        模块——不补边则 compile_sheet 进页即报 V1（模块未收束）假阳性。"""
        graph = sheet.setdefault("graph", {"nodes": [], "edges": []})
        ids, placed = [], []
        for i, sug in enumerate(reverse_compile(clip)):
            col, row = i % 3, i // 3
            node = new_node(str(sug["key"]),
                            col * (CARD_W + 48) + CARD_W / 2 + 40,
                            row * (CARD_H + 44) + CARD_H / 2 + 40,
                            dict(sug.get("params") or {}))
            graph.setdefault("nodes", []).append(node)
            ids.append(str(node["id"]))
            placed.append({"id": str(node["id"]), "key": str(sug["key"]),
                           "params": dict(sug.get("params") or {}),
                           "role": str(sug.get("role", "solo"))})
        for w in wire_reverse_edges(placed):
            graph.setdefault("edges", []).append(
                new_edge(w["from"], w["to"], "out", str(w.get("to_port", "in"))))
        return ids
