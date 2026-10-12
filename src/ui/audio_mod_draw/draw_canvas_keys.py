# -*- coding: utf-8 -*-
"""Draw 画布键盘事件（R182）—— 从 draw_canvas 拆出（文件行数门禁）。

Del=删除选中 / Home=全览 / A=全选；与画布状态机（宿主属性）鸭子协作。
R183 4.1：Ctrl+Z=撤销 / Ctrl+Y、Ctrl+Shift+Z=重做（路由进 `DrawUndoMixin`，
含文本焦点守卫，照 `main_window_shortcuts._text_focus` 先例）；`event` 拦
ShortcutOverride——否则主窗编辑菜单的撤销 QAction（StandardKey.Undo）先于
keyPressEvent 截走 Ctrl+Z，画布栈永远收不到。
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt


class CanvasKeysMixin:
    """键盘交互混入（宿主：DrawCanvas）。"""

    def event(self, ev):
        """画布聚焦时接管 Ctrl+Z/Y 的快捷键匹配（其余事件照常走基类）。"""
        if ev.type() == QEvent.Type.ShortcutOverride and self._shortcut_override(ev):
            return True
        return super().event(ev)

    def keyPressEvent(self, ev):
        if self._undo_key(ev):          # 撤销/重做路由（内含焦点守卫）
            ev.accept()
            return
        if ev.key() == Qt.Key.Key_Delete and self._sel_ids:
            self._delete_selected()     # DrawUndoMixin：多删单命令入栈
            ev.accept()
            return
        if ev.key() == Qt.Key.Key_Home:
            self.zoom_to_fit()
            ev.accept()
            return
        if ev.key() == Qt.Key.Key_A:
            self._sel_ids = {str(n.get("id", ""))
                             for n in self._graph.get("nodes") or []}
            self.selection_changed.emit(next(iter(self._sel_ids), ""))
            self.update()
            ev.accept()
            return
        super().keyPressEvent(ev)
