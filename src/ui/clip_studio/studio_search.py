# -*- coding: utf-8 -*-
"""画布内搜索（R183 W2-ST #9）—— 纯匹配函数 + 极简浮层控件。

匹配口径（任务书）：`key` 与卡名（中文 name）**大小写不敏感子串**；按节点表原序
返回（保持「目录顺序」直觉）。`limit` 防超大图一次命中过多（默认 12）。
浮层是 `QLineEdit` 子类：Enter → `submitted`、Esc → `cancelled`（画布负责
聚焦/高亮，控件不反向依赖画布 —— 便于无 GUI 单测）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit

from src.ui.clip_studio.studio_note import lookup_spec

__all__ = ["MATCH_LIMIT", "match_nodes", "spec_name", "SearchBoxWidget"]

#: 单次搜索最多返回的命中数。
MATCH_LIMIT = 12
_PLACEHOLDER = "搜索卡片（键/名称）"


def spec_name(node) -> str:
    """节点显示名（卡目录中文名；未知 key → key 本身）。"""
    spec = lookup_spec((node or {}).get("key", ""))
    return str(getattr(spec, "name", "") or (node or {}).get("key", ""))


def _matches(nodes, needle: str, limit: int) -> list:
    """子串匹配（生成器过滤；limit 到点即停 —— 压 CC/提前退出）。"""
    out = []
    for n in nodes or ():
        if not isinstance(n, dict):
            continue
        hay = f"{n.get('key', '')}\n{spec_name(n)}".lower()
        nid = str(n.get("id", ""))
        if needle in hay and nid:
            out.append(nid)
            if len(out) >= limit:
                break
    return out


def match_nodes(nodes, query, limit: int = MATCH_LIMIT) -> list:
    """按 key/名称子串匹配 → 命中节点 id 列表（原表顺序）；空查询 → ``[]``。

    `nodes` = 节点 dict 列表（畸形项跳过）；大小写不敏感。
    """
    needle = str(query or "").strip().lower()
    return [] if not needle else _matches(nodes, needle, int(limit))


class SearchBoxWidget(QLineEdit):
    """浮层搜索框：Enter 提交、Esc 取消（画布负责显隐与定位）。"""

    submitted = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(_PLACEHOLDER)
        self.setClearButtonEnabled(True)
        self.setFixedHeight(24)
        self.setMinimumWidth(180)

    def keyPressEvent(self, ev):  # noqa: N802
        key = ev.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit()
            ev.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)
