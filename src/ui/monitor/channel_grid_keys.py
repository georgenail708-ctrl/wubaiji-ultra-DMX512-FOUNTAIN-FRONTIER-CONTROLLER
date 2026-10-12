# -*- coding: utf-8 -*-
"""ChannelGrid 键盘导航（R183 W2；深查② §四 #12）。

从 channel_grid.py 拆出（文件 ≤500 行门禁）。方向键移动游标（跨折叠行跳
过）、Shift 扩选、Home/End（Ctrl 变体=全域首尾）。纯计算委托
``monitor_keynav``，本模块只管选择集/信号/重绘的宿主侧收尾。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

from .monitor_keynav import extend_selection, home_end, next_channel

_QT_DIR = {
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
}
_QT_EDGE = {Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end"}


class ChannelGridKeysMixin:
    """方向键/Home/End/Shift 扩选（混入宿主类；选择集状态在宿主）。"""

    def _handle_nav_key(self, e) -> bool:
        """处理导航键；返回是否已消费（未消费让调用方走 super）。"""
        key = e.key()
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        name = _QT_DIR.get(key)
        if name is not None:
            self._move_cursor(name, shift)
            return True
        edge = _QT_EDGE.get(key)
        if edge is not None:
            self._move_edge(edge, ctrl=ctrl, shift=shift)
            return True
        return False

    def _cursor_or_default(self) -> int:
        """当前游标：键盘游标 → 最近点击（鼠标锚点）→ 选区末端 → 通道 1。"""
        for cand in (getattr(self, "_cursor", 0),
                     getattr(self, "_last_clicked", 0),
                     max(getattr(self, "_selected", None) or {0})):
            if 1 <= int(cand or 0) <= 512:
                return int(cand)
        return 1

    def _move_cursor(self, direction: str, shift: bool) -> None:
        cur = self._cursor_or_default()
        target = next_channel(cur, direction, collapsed=self._collapsed)
        self._apply_cursor(target, shift)

    def _move_edge(self, edge: str, *, ctrl: bool, shift: bool) -> None:
        target = home_end(self._cursor_or_default(), edge, ctrl=ctrl)
        self._apply_cursor(target, shift)

    def _apply_cursor(self, target: int, shift: bool) -> None:
        """落点：Shift=从锚点扩选（保留原选择），否则单点选择。"""
        if shift:
            anchor = self._cursor_or_default()
            self._selected = extend_selection(self._selected, anchor, target)
        else:
            self._selected = {int(target)}
            self._last_clicked = int(target)
        self._cursor = int(target)
        self.selection_changed.emit(self._selected.copy())
        self.update()
