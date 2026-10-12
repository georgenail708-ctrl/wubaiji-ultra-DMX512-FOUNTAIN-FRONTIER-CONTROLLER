# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_proxy.py — R183 W2-FX：搜索期即时过滤判据（深查② §一 #16）。

这是 **QSortFilterProxyModel 判据层**（Qt 官方过滤语义）。落地在
``effect_browser_w2.apply_instant_filter``（视图行隐藏）——为什么不是把 proxy
直接装成 view model，见该函数 docstring（本仓视图多处直接用源模型行号调
``visualRect``/``setCurrentIndex``，换 view model 会让这些既有路径失效并波及
批 A 的星标命中与定测）。

★ 判据只有一份真源：`effect_categories.preset_matches_query`（含"旧朴素 AND 优先"
  回退），因此代理结果与最终权威重建（``plan_preset_rows(query=...)``）逐条一致。
  本模块负责从源模型行号取到**预设对象**（``PresetListModel._flat_items``）；
  取不到（测试替身/未来换模型）→ 回退按索引角色拼 hay，仍调用同一判定函数。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt

logger = logging.getLogger(__name__)

#: 与 effect_browser 的角色号严格一致
_ITEM_ROLE = Qt.ItemDataRole.UserRole
_ITEM_TYPE_ROLE = Qt.ItemDataRole.UserRole + 100
_CAT_NAME_ROLE = Qt.ItemDataRole.UserRole + 101
_DESC_ROLE = Qt.ItemDataRole.UserRole + 104


class _IndexPreset:
    """兜底"预设替身"：模型不暴露 ``_flat_items`` 时，用角色数据喂判定函数。"""

    __slots__ = ("id", "name", "description", "category", "tags")

    def __init__(self, index):
        self.id = str(index.data(_ITEM_ROLE) or "")
        self.name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        self.description = str(index.data(_DESC_ROLE) or "")
        self.category = str(index.data(_CAT_NAME_ROLE) or "")
        self.tags = []


def preset_at(model, row: int):
    """源模型第 ``row`` 行的预设对象；标题行/取不到 → None。"""
    try:
        entry = model._flat_items[int(row)]
        return entry[1] if entry and entry[0] == "item" else None
    except Exception:
        return None


def row_is_header(model, row: int) -> bool:
    """该行是否分组头（取不到结构信息 → 按条目类型角色判断）。"""
    try:
        entry = model._flat_items[int(row)]
        return not entry or entry[0] != "item"
    except Exception:
        try:
            idx = model.index(int(row), 0)
            return str(idx.data(_ITEM_TYPE_ROLE) or "") != "item"
        except Exception:
            return True


def row_matches(model, row: int, query: str) -> bool:
    """第 ``row`` 行是否命中查询（**与权威筛选同一函数**；分组头恒 True）。"""
    try:
        preset = preset_at(model, row)
        if preset is None:
            return True
        from .effect_categories import preset_matches_query
        return bool(preset_matches_query(preset, query))
    except Exception:
        logger.debug("effect_browser_w2_proxy: row match failed at %s", row,
                     exc_info=True)
        return True


class PresetFilterProxy(QSortFilterProxyModel):
    """查询词驱动的条目过滤（分组头恒通过）。

    ``set_query`` 只在查询串变化时返回 True（未变 → 不触发重排）；
    ``filterAcceptsRow`` 委托 ``effect_categories.preset_matches_query``。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""

    def set_query(self, query) -> bool:
        """设置查询串；返回是否发生变化。"""
        text = str(query or "").strip()
        if text == self._query:
            return False
        self._query = text
        try:
            self.invalidateFilter()
        except Exception:
            logger.debug("effect_browser_w2_proxy: invalidateFilter failed",
                         exc_info=True)
        return True

    def query(self) -> str:
        """当前查询串（定测/诊断用）。"""
        return str(self._query)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._query:
            return True
        try:
            model = self.sourceModel()
            if model is None:
                return True
            return row_matches(model, source_row, self._query)
        except Exception:
            logger.debug("effect_browser_w2_proxy: filterAcceptsRow failed",
                         exc_info=True)
            return True
