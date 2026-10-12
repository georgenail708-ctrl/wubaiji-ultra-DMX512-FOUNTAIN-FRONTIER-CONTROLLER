# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_view.py — R183 W2-FX：列表视图侧的四件事（#12/#16/#10/#14）。

  · #16 搜索期即时查收窄 —— 判据用 ``QSortFilterProxyModel``（``w2_proxy``），
    落地用**视图行隐藏**：防抖 220ms 到点前用户已经能看到结果，到点后仍由
    ``_populate_list`` 做权威重建（跨大类检索语义一个字不改）。
    不用"把 proxy 装成 view model"的原因见 ``apply_instant_filter`` docstring。
  · #12 不兼容卡片淡出 —— 与当前设备通道无交集的预设只"降透明度"而**不隐藏**
    （隐藏会与筛选语义混淆：筛选 = 看不见；本项 = 看得见但不适用）。
  · #10 热度优先排序 —— 组内按使用次数降序重排（稳定排序，绝不丢行）。
  · #14 缩略开关 + 列表重建后收口（代理查询同步 / 缩略缓存失效 / 兼容集重算）。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Set

from PyQt6.QtCore import QModelIndex, Qt

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_paint as _paint
from . import effect_browser_w2_proxy as _proxy
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)

#: 空父索引（代理判据用；模块级只构造一次）
_EMPTY_INDEX = QModelIndex()


# ══════════════════════════════════════════════════════════════════════════
#  #16 搜索期即时收窄
# ══════════════════════════════════════════════════════════════════════════

def apply_instant_filter(panel, text) -> Set[str]:
    """按查询即时收窄可见行；返回新的可见 id 集（无代理/无模型 → 原样返回）。

    ★ 为什么不是"把 proxy 装成 view model"：本仓视图的选中恢复与星标命中都
      直接用 ``_preset_model``（源模型）的行号调 ``visualRect``/``setCurrentIndex``
      （见 ``_restore_selected_preset`` 与 ``PresetListView.mousePressEvent``），
      换 view model 会让这些既有路径拿到跨模型索引而失效（并波及批 A 定测）。
      故：**判据**走 QSortFilterProxyModel（Qt 官方过滤语义），**落地**走行隐藏。
    """
    model = getattr(panel, "_preset_model", None)
    proxy = getattr(panel, "_w2_proxy", None)
    view = getattr(panel, "_preset_list", None)
    all_ids = item_ids(model)
    if model is None or proxy is None or view is None:
        return all_ids
    query = str(text or "").strip()
    proxy.set_query(query)
    hidden = hidden_rows(model, query, panel)
    apply_hidden(panel, hidden)
    keep = kept_ids(model, hidden)
    panel._visible_preset_ids = keep
    return keep


def item_ids(model) -> Set[str]:
    """模型里全部预设 id（无模型 → 空集）。"""
    return {pid for _row, pid in row_id_items(model)}


def row_id_items(model) -> List[tuple]:
    """模型里全部 (行号, 预设 id)（标题行/空 id 跳过）。"""
    if model is None:
        return []
    out: List[tuple] = []
    try:
        for row in range(model.rowCount()):
            pid = str(model.index(row, 0).data(Qt.ItemDataRole.UserRole) or "")
            if pid:
                out.append((row, pid))
    except Exception:
        logger.debug("effect_browser_w2_view: enumerate rows failed", exc_info=True)
    return out


def hidden_rows(model, query: str, panel=None) -> Set[int]:
    """该查询下应隐藏的行集合（条目 + 段内无命中的标题行）；空查询 → 空集。

    判据经 ``proxy.filterAcceptsRow``（``QSortFilterProxyModel`` 的官方入口）取，
    而非本地重算 —— "即时收窄"与"代理语义"永远同一条真源。
    """
    if not query:
        return set()
    proxy = getattr(panel if panel is not None else _panel_of(model), "_w2_proxy", None)
    if proxy is None:
        return set()
    return _hidden_from(_accepted_rows(model, proxy))


def _panel_of(model) -> object:
    """从模型反查面板（代理挂在面板上；取不到 → 空对象）。"""
    try:
        return model.parent()
    except Exception:
        return None


def _accepted_rows(model, proxy) -> List[Optional[bool]]:
    """每行是否被接受（分组头 = None）。"""
    out: List[Optional[bool]] = []
    for row in range(_row_count(model)):
        if _proxy.row_is_header(model, row):
            out.append(None)
        else:
            out.append(_accepts(proxy, row))
    return out


def _accepts(proxy, row: int) -> bool:
    """代理判定（异常 → 接受 = 宁可不隐藏，绝不让列表凭空少行）。"""
    try:
        return bool(proxy.filterAcceptsRow(int(row), _EMPTY_INDEX))
    except Exception:
        logger.debug("effect_browser_w2_view: proxy accepts failed at %s", row)
        return True


def kept_ids(model, hidden: Set[int]) -> Set[str]:
    """隐藏后仍可见的预设 id 集。"""
    return {pid for row, pid in row_id_items(model) if row not in hidden}


def _hidden_from(accepted) -> Set[int]:
    """由"逐行接受表"推出隐藏集合（含空分组头剪枝）。"""
    hidden = {row for row, val in enumerate(accepted) if val is False}
    hidden |= empty_header_rows(accepted)
    return hidden


def empty_header_rows(accepted) -> Set[int]:
    """段内无任何被接受条目的标题行（否则会留一个空分组头）。"""
    out: Set[int] = set()
    seg: Optional[int] = None
    total = len(accepted)
    for row in range(total + 1):
        is_head = _is_head(accepted, row, total)
        if not is_head:
            continue
        if seg is not None and not any(accepted[seg + 1:row]):
            out.add(seg)
        seg = None if row >= total else row
    return out


def _is_head(accepted, row: int, total: int) -> bool:
    if row >= total:
        return True
    return accepted[row] is None


def apply_hidden(panel, hidden: Set[int]) -> None:
    """把隐藏集合落到视图（只对状态变化的行调 ``setRowHidden``）。"""
    view = getattr(panel, "_preset_list", None)
    model = getattr(panel, "_preset_model", None)
    if view is None or model is None:
        return
    prev = getattr(panel, "_w2_hidden_rows", None)
    prev = set() if not isinstance(prev, set) else prev
    for row in range(_row_count(model)):
        want = row in hidden
        if (row in prev) == want:
            continue
        _set_row_hidden(view, row, want)
    panel._w2_hidden_rows = set(hidden)


def _row_count(model) -> int:
    try:
        return int(model.rowCount())
    except Exception:
        return 0


def _set_row_hidden(view, row: int, hidden: bool) -> None:
    try:
        view.setRowHidden(int(row), bool(hidden))
    except Exception:
        logger.debug("effect_browser_w2_view: setRowHidden failed at %s", row)


# ══════════════════════════════════════════════════════════════════════════
#  #12 目标通道不兼容 → 淡出（绘制侧只做暗罩）
# ══════════════════════════════════════════════════════════════════════════

#: 不兼容集合缓存（清查⑧ 性能：load_state/列表重建每次重扫 1223 预设 ×
#: 目标通道实测 81ms；键 = 可用通道集合 frozenset，同集合内直接命中；
#: 预设库编辑（编辑标签等）显式失效见 invalidate_incompatible）
_INCOMPAT_CACHE: dict = {"key": None, "bad": set()}


def invalidate_incompatible() -> None:
    """预设库/目标声明变化时清缓存（编辑标签、导入预设包后调用）。"""
    _INCOMPAT_CACHE["key"] = None
    _INCOMPAT_CACHE["bad"] = set()


def refresh_incompatible(panel) -> int:
    """重算不兼容预设 id 集（无可用通道信息 → 空集 = 全亮）；返回数量。

    缓存键 = (可用通道集合, 预设总数)——预设数变化（另存/删除/导入）自动失效。
    """
    avail = available_channel_types(panel)
    try:
        n_presets = len(_base.engine().all_presets())
    except Exception:
        n_presets = -1
    key = (frozenset(avail), n_presets)
    if _INCOMPAT_CACHE.get("key") == key:
        bad = _INCOMPAT_CACHE.get("bad") or set()
    else:
        bad = incompatible_ids(avail)
        _INCOMPAT_CACHE["key"] = key
        _INCOMPAT_CACHE["bad"] = bad
    panel._w2_incompatible = bad
    return len(bad)


def incompatible_ids(avail: Set[str]) -> Set[str]:
    """与 ``avail`` 无交集的预设 id 集（预设未声明 target_channels → 视为通用）。"""
    if not avail:
        return set()
    return {pid for pid, targets in _preset_targets() if _incompatible(targets, avail)}


def _preset_targets():
    """[(预设 id, target_channels)]（引擎不可用 → 空表）。"""
    try:
        return [(str(getattr(p, "id", "") or ""),
                 [str(c) for c in (getattr(p, "target_channels", None) or ())])
                for p in _base.engine().all_presets()]
    except Exception:
        logger.debug("effect_browser_w2_view: preset scan failed", exc_info=True)
        return []


def _incompatible(targets, avail: Set[str]) -> bool:
    """该预设是否与可用通道互斥（无声明/有交集 → False）。"""
    if not targets:
        return False
    return not any(_matches(ch, targets) for ch in avail)


def _matches(ch_type: str, targets) -> bool:
    """走主面板的通道等价判定（``_channel_matches``），取不到则做严格相等。"""
    try:
        from .effect_browser import _channel_matches
        return bool(_channel_matches(ch_type, targets))
    except Exception:
        return ch_type in {str(t) for t in targets}


def available_channel_types(panel) -> Set[str]:
    """当前选中设备实际存在的通道类型集合（取不到 → 空集）。"""
    fixtures, defs = _fixture_inputs(panel)
    out: Set[str] = set()
    for fixture in fixtures:
        out.update(_def_types(defs.get(str(getattr(fixture, "def_id", "")))))
    return out


def _fixture_inputs(panel):
    """(选中设备前 200 台, {定义 id: 定义})；取不到 → ([], {})。"""
    try:
        fixtures = list(panel._get_active_fixtures() or [])[:200]
        raw = getattr(panel._project_object(), "fixture_defs", None) or ()
    except Exception:
        logger.debug("effect_browser_w2_view: fixture inputs failed")
        return [], {}
    defs = dict(raw) if isinstance(raw, dict) else {
        str(getattr(fd, "id", "")): fd for fd in raw}
    return fixtures, defs


def _def_types(fixture_def) -> Set[str]:
    """该定义的全部通道类型（规范化后）。"""
    out: Set[str] = set()
    for ch in (getattr(fixture_def, "channels", None) or ()):
        value = _norm(ch)
        if value:
            out.add(value)
    return out


def _norm(ch_type) -> str:
    try:
        from .effect_browser import _norm_channel_type
        return str(_norm_channel_type(ch_type))
    except Exception:
        return str(ch_type or "").strip().lower()


# ══════════════════════════════════════════════════════════════════════════
#  #10 热度优先排序
# ══════════════════════════════════════════════════════════════════════════

def set_heat_first(panel, on: bool) -> None:
    """开启/关闭「热度优先」并立即生效（关掉 = 走既有重建路径，排序回默认）。"""
    _store.set_pref("heat_first", bool(on))
    if on:
        apply_heat_order(panel)
    else:
        _base.refresh_list(panel)


def apply_heat_order(panel) -> int:
    """组内按使用次数降序重排；返回参与重排的条目数（无变更 → 0）。"""
    model = getattr(panel, "_preset_model", None)
    flat = getattr(model, "_flat_items", None)
    if not isinstance(flat, list):
        return 0
    out, moved = reorder_flat(flat, _store.heat_rank_map())
    if not moved:
        return 0
    _replace_flat(model, out)
    _restore_selection(panel)
    return moved


def reorder_flat(flat, rank) -> tuple:
    """纯函数：组内按 ``rank`` 稳定排序 → ``(新扁平表, 参与条目数)``。"""
    out: List = []
    seg: List = []
    moved = 0
    for entry in list(flat) + [None]:
        if entry is not None and entry[0] == "item":
            seg.append(entry)
            continue
        if seg:
            seg.sort(key=lambda e: rank.get(str(getattr(e[1], "id", "")), 1 << 30))
            moved += len(seg)
            out.extend(seg)
            seg = []
        if entry is not None:
            out.append(entry)
    return out, moved


def _replace_flat(model, flat) -> None:
    try:
        model.beginResetModel()
        model._flat_items = flat
        model.endResetModel()
    except Exception:
        logger.debug("effect_browser_w2_view: replace flat failed", exc_info=True)


def _restore_selection(panel) -> None:
    try:
        from .effect_browser import _restore_selected_preset
        _restore_selected_preset(panel)
    except Exception:
        logger.debug("effect_browser_w2_view: restore selection failed")


# ══════════════════════════════════════════════════════════════════════════
#  #14 缩略开关 + 列表刷新后收口
# ══════════════════════════════════════════════════════════════════════════

def set_thumb_anim(panel, on: bool) -> None:
    """开/关卡片缩略动画（关掉清缓存 → 绘制层零计算）。"""
    _store.set_pref("thumb_anim", bool(on))
    _paint.invalidate_thumbs(panel)
    _update_viewport(panel)


def _update_viewport(panel) -> None:
    view = getattr(panel, "_preset_list", None)
    if view is None:
        return
    try:
        view.viewport().update()
    except Exception:
        logger.debug("effect_browser_w2_view: viewport update failed")


def refresh_after_populate(panel) -> None:
    """列表重建后收口：#16 行隐藏复位 / #12 兼容集 / #10 热度 / #14 缩略缓存。"""
    try:
        apply_hidden(panel, set())
        refresh_incompatible(panel)
        if _store.pref("heat_first", False):
            apply_heat_order(panel)
        _sync_proxy_query(panel)
        _paint.invalidate_thumbs(panel)
        _update_viewport(panel)
    except Exception:
        logger.debug("effect_browser_w2_view: refresh after populate failed",
                     exc_info=True)


def _sync_proxy_query(panel) -> None:
    proxy = getattr(panel, "_w2_proxy", None)
    if proxy is None:
        return
    try:
        proxy.set_query(_base.search_text(panel))
    except Exception:
        logger.debug("effect_browser_w2_view: proxy query sync failed")


__all__ = ["apply_heat_order", "apply_hidden", "apply_instant_filter",
           "available_channel_types", "empty_header_rows", "hidden_rows",
           "incompatible_ids", "item_ids", "kept_ids", "refresh_after_populate",
           "refresh_incompatible", "reorder_flat", "row_id_items",
           "set_heat_first", "set_thumb_anim"]
_ = Optional
