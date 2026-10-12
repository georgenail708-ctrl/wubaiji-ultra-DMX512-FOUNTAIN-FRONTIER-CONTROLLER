# -*- coding: utf-8 -*-
"""画布键位混入（R172/R173 键表 + R183 W2-ST 工具键声明与分发）。

从 `studio_canvas_interact` 拆出（该文件贴 500 行门禁）。

`ShortcutOverride` 拦截的动机（R173 定案，勿改）：`main_window.py` 把 Home/F/X/Del
注册成全局 `QAction`（`WindowShortcut` 语境），会抢在本控件 `keyPressEvent` 之前
吃掉按键；god-class 冻结不改 main_window，故照 `topview_panel.py` 先例在 `event()`
里声明接管。**未聚焦时不拦截** —— 画布不持焦点时全局快捷键不受影响。

R183 W2-ST 工具键：Ctrl+F 搜索 / Ctrl+L·R·T·B 对齐 / Ctrl+D 均分 / Ctrl+G 收束 /
M 禁用切换 / +/-/0 缩放（`_claimed_key` 为唯一判据表，见 `studio_canvas_tools`）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

__all__ = ["CanvasKeysMixin"]


class CanvasKeysMixin:
    """键盘事件（宿主 = ClipStudioCanvas；`_CLAIM_KEYS` 供工具层判据复用）。"""

    _CLAIM_KEYS = (Qt.Key.Key_Home, Qt.Key.Key_F, Qt.Key.Key_X, Qt.Key.Key_Delete,
                   Qt.Key.Key_Space)

    def event(self, ev):  # noqa: N802
        """ShortcutOverride 声明接管（画布键 + 工具键 + 拖线中的 Esc）。"""
        from PyQt6.QtCore import QEvent
        if ev.type() == QEvent.Type.ShortcutOverride and (
                self._claimed_key(ev)
                or (ev.key() == Qt.Key.Key_Escape and self._link is not None)):
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape and self._link is not None:
            self._cancel_link()             # Esc 取消拖线（拖线中抢占全局「停止」Esc）
            ev.accept()
            return
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space_down = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            ev.accept()
            return
        if self._try_tools_keys(ev.key(), ev.modifiers()):   # 工具键（含 Ctrl 表）
            ev.accept()
            return
        if self._try_studio_key(ev.key(), ev.modifiers()):
            ev.accept()
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space_down = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            ev.accept()
            return
        super().keyReleaseEvent(ev)

    def _try_studio_key(self, key, mods) -> bool:
        """剪贴板 / 删除 / 视图键分发；命中返回 True（各段拆函数降 CC）。"""
        return (self._try_clipboard_keys(key, mods)
                or self._try_delete_key(key)
                or self._try_view_keys(key, mods))

    def _try_delete_key(self, key) -> bool:
        """X·Del 删除选中（卡/边优先；只有分组框被选中时删框）。"""
        if key not in (Qt.Key.Key_Delete, Qt.Key.Key_X):
            return False
        if self._sel_nodes or self._sel_node or self._sel_edge:
            self._delete_selected()
            return True
        if self._sel_frame:
            self._delete_frame_selected()
            return True
        return False

    def _delete_frame_selected(self) -> None:
        """删选中分组框：框消失、框内卡片保留（同款）；清理参考线。"""
        if self.delete_frame(self._sel_frame):
            self._commit_graph_change()

    def _try_view_keys(self, key, mods) -> bool:
        """Home 全览 / F 聚焦选中 / A 全选 / Alt+A 取消全选。"""
        if key == Qt.Key.Key_Home:
            self.zoom_to_fit()
            return True
        if key == Qt.Key.Key_F and self._sel_node:
            self.focus_node(self._sel_node)
            return True
        if key == Qt.Key.Key_A:
            self.deselect_all() if mods & Qt.KeyboardModifier.AltModifier else self.select_all()
            return True
        return False

    def _try_clipboard_keys(self, key, mods) -> bool:
        """Shift+D 原地复制 / Ctrl+C 复制 / Ctrl+V 粘贴（实现在 studio_clipboard）。"""
        from src.ui.clip_studio import studio_clipboard as _cb
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_D and shift and not ctrl:
            _cb.duplicate_selected(self)
            return True
        if key == Qt.Key.Key_C and ctrl and not shift:
            _cb.copy_selected(self)
            return True
        if key == Qt.Key.Key_V and ctrl and not shift:
            _cb.paste_clipboard(self)
            return True
        return False

    def _delete_selected(self) -> None:
        """Del/X：批量删多选卡（连带删相连边）或删选中边——只发一次图变更
        （R173 Group2 硬约束：批删=一条撤销记录，靠 `_delete_nodes` 单次图变更实现）。

        `_sel_nodes` 为空但 `_sel_node` 非空 → 兼容直接赋值 `cv._sel_node = nid`
        的旧探针/测试用法（`_probe_r173_studio_session.py`），退化为单删。"""
        nids = set(self._sel_nodes) or ({self._sel_node} if self._sel_node else set())
        if nids:
            self._delete_nodes(nids)
            self._clear_selection()
            self.selection_changed.emit("")
        elif self._sel_edge:
            graph = self._sheet["graph"]
            graph["edges"] = [e for e in graph.get("edges", [])
                              if str(e.get("id")) != self._sel_edge]
            self._sel_edge = ""
        self._commit_graph_change()
        self.update()
