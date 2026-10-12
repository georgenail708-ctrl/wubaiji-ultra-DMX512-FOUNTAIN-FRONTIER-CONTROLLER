# -*- coding: utf-8 -*-
"""TopView 参考线模型（R183/F2 · 深查③ T2）。

从 `topview_rulers` 拆出（新文件 500 行硬限）：参考线的增删改查与持久化落在
**当前页 dict 的自由键 `guides`** —— `TopViewPanel.save_state` 把 `_pages`
整棵子树落盘、`load_state` 整棵灌回，故零改动即随工程保存（旧工程缺该键时
`items()` 返回空表，天然向后兼容）。

数据结构：`[[axis, pos, locked], ...]`，`axis="x"` 为竖直参考线（位置=世界 x）、
`"y"` 为水平参考线。锁定线不参与拖动（单击解锁，见标尺交互）。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PyQt6.QtGui import QColor

from . import qascade_theme as _th

#: 参考线颜色（解锁=焦点蓝虚线，锁定=警示黄实线）。
GUIDE_COLOR = _th.FOCUS_BLUE
GUIDE_LOCKED_COLOR = _th.BRIGHT_WARN


class GuideSet:
    """参考线集合（axis: "x" 为竖直参考线，位置=世界 x；"y" 为水平参考线）。

    持久化落在当前页 dict 的 `guides` 自由键：[[axis, pos, locked], ...]。
    """

    def __init__(self, page_getter) -> None:
        self._page_getter = page_getter

    def _page(self) -> Optional[dict]:
        try:
            return self._page_getter()
        except Exception:
            return None

    def _raw(self) -> list:
        page = self._page()
        if page is None:
            return []
        raw = page.get("guides")
        return raw if isinstance(raw, list) else []

    def items(self) -> List[dict]:
        out: List[dict] = []
        for entry in self._raw():
            try:
                axis, pos, locked = entry[0], float(entry[1]), bool(entry[2])
            except Exception:
                continue
            if str(axis) not in ("x", "y"):
                continue
            out.append({"axis": str(axis), "pos": pos, "locked": locked})
        return out

    def _store(self, items: Sequence[dict]) -> None:
        page = self._page()
        if page is None:
            return
        page["guides"] = [[str(g.get("axis")), float(g.get("pos", 0.0)),
                           bool(g.get("locked", False))] for g in items]

    def add(self, axis: str, pos: float, locked: bool = False) -> Optional[dict]:
        if str(axis) not in ("x", "y"):
            return None
        guide = {"axis": str(axis), "pos": float(pos), "locked": bool(locked)}
        items = self.items()
        items.append(guide)
        self._store(items)
        return guide

    def move(self, guide: dict, pos: float) -> bool:
        if guide.get("locked"):
            return False
        items = self.items()
        for g in items:
            if g["axis"] == guide.get("axis") and abs(g["pos"] - float(guide.get("pos", 0.0))) < 1e-6:
                g["pos"] = float(pos)
                guide["pos"] = float(pos)
                self._store(items)
                return True
        return False

    def set_locked(self, guide: dict, locked: bool) -> bool:
        items = self.items()
        for g in items:
            if g["axis"] == guide.get("axis") and abs(g["pos"] - float(guide.get("pos", 0.0))) < 1e-6:
                g["locked"] = bool(locked)
                guide["locked"] = bool(locked)
                self._store(items)
                return True
        return False

    def remove(self, guide: dict) -> bool:
        items = self.items()
        keep = [g for g in items if not (
            g["axis"] == guide.get("axis")
            and abs(g["pos"] - float(guide.get("pos", 0.0))) < 1e-6)]
        if len(keep) == len(items):
            return False
        self._store(keep)
        return True

    def remove_near(self, axis: str, pos: float, tol: float) -> Optional[dict]:
        """删除容差内最近的一条（右键菜单用），返回被删项。"""
        best, best_d = None, float(tol)
        for g in self.items():
            if g["axis"] != axis:
                continue
            d = abs(g["pos"] - float(pos))
            if d <= best_d:
                best, best_d = g, d
        if best is None:
            return None
        self.remove(best)
        return best

    def nearest(self, axis: str, pos: float, tol: float) -> Optional[dict]:
        """容差内最近的一条（拖动命中/锁定切换用）。"""
        best, best_d = None, float(tol)
        for g in self.items():
            if g["axis"] != axis:
                continue
            d = abs(g["pos"] - float(pos))
            if d <= best_d:
                best, best_d = g, d
        return best

    def axis_values(self, axis: str) -> List[float]:
        return [g["pos"] for g in self.items() if g["axis"] == axis]

    def clear(self) -> int:
        n = len(self.items())
        self._store([])
        return n


def guide_draw_color(guide: dict) -> QColor:
    return QColor(GUIDE_LOCKED_COLOR if guide.get("locked") else GUIDE_COLOR)


