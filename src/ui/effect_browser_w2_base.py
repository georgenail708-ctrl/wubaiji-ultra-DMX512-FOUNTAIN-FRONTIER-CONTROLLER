# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_base.py — R183 W2-FX 效果浏览器扩展的**共享底座**。

W2-FX（深查② §一 剩余项）被拆成多个 ≤500 行的功能模块（门禁「新文件 ≤500 行」），
本模块放它们共用的只读工具：取引擎 / 参数区间 / 参数默认值 / 状态栏 / 列表刷新。

★ 全部函数只读或只调既有面板 API，不自己维护第二份状态；
★ 每个函数自带异常兜底（增益功能绝不把异常抛回 Qt 事件循环或 40fps 热路径）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 结构/全局开关：不算"用户改的参数"，也不参与微扰与宏折算
NON_PARAM_KEYS = frozenset({
    "spatial_scope", "control_mode", "_skip_base_order", "_sort_mode",
    "_app_direction", "_app_scope", "_joy_amp_axis", "_global_speed_factor",
    "fixture_ids", "group_ids", "base_order_ids", "subset_ids", "unit_groups",
})
#: 宏/微扰都只碰数值参数
NUMERIC_TYPES = ("float", "int")


def engine():
    """效果引擎单例（延迟 import，避免与主模块形成 import 环）。"""
    from .effect_engine import get_engine
    return get_engine()


def num(value) -> Optional[float]:
    """数值容错（非数值 → None）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def preset_of(panel):
    """当前选中预设对象（未选中/取不到 → None）。"""
    preset_id = str(getattr(panel, "_selected_id", "") or "")
    if not preset_id:
        return None
    try:
        return engine().get_preset(preset_id)
    except Exception:
        logger.debug("effect_browser_w2: preset lookup failed", exc_info=True)
        return None


def preset_id_of(panel) -> str:
    return str(getattr(panel, "_selected_id", "") or "")


def spec_map(panel) -> Dict[str, Any]:
    """当前预设的 {参数键: ParamSpec}（无预设 → 空表）。"""
    preset = preset_of(panel)
    out: Dict[str, Any] = {}
    for spec in (getattr(preset, "params", None) or ()):
        key = str(getattr(spec, "key", "") or "")
        if key:
            out[key] = spec
    return out


def _skip_span_key(key: str) -> bool:
    return key in NON_PARAM_KEYS


def spec_span(spec) -> Optional[tuple]:
    """单个数值参数的 (lo, hi)；非数值/区间退化 → None。"""
    if str(getattr(spec, "type", "") or "").lower() not in NUMERIC_TYPES:
        return None
    lo = num(getattr(spec, "min_val", None))
    hi = num(getattr(spec, "max_val", None))
    if lo is None or hi is None:
        return None
    return (lo, hi) if hi > lo else None


def spec_spans(panel) -> Dict[str, tuple]:
    """当前预设全部数值参数的允许区间 {key: (lo, hi)}（微扰/宏共用真源）。"""
    ch_key = str(getattr(panel, "_channel_param_key", "") or "")
    out: Dict[str, tuple] = {}
    for key, spec in spec_map(panel).items():
        if _skip_span_key(key):
            continue
        if key == ch_key:
            continue
        span = spec_span(spec)
        if span is not None:
            out[key] = span
    return out


def spec_defaults(panel) -> Dict[str, Any]:
    """当前预设数值参数的默认值 {key: default}（宏的中性位）。"""
    out: Dict[str, Any] = {}
    for key, spec in spec_map(panel).items():
        if _skip_span_key(key):
            continue
        val = num(getattr(spec, "default", None))
        if val is not None:
            out[key] = val
    return out


def default_value_for(spec):
    """单参数的"预设默认值"（与 ``_reset_current_params`` 同口径）。"""
    typ = str(getattr(spec, "type", "") or "").lower()
    if typ in NUMERIC_TYPES:
        return getattr(spec, "default", 0)
    if typ == "bool":
        return bool(getattr(spec, "default", False))
    if typ == "choice":
        default = getattr(spec, "default", "")
        if isinstance(default, (list, tuple)):
            return None          # 通道列表型默认值由表单逻辑管理，不做整体复位
        return default
    return None


def widget_value(panel, key: str):
    """控件树里该参数的当前值（取不到 → None）。"""
    pw = (getattr(panel, "_param_widgets", None) or {}).get(str(key))
    if pw is None:
        return None
    try:
        return pw.get_value()
    except Exception:
        return None


def set_status(panel, text: str) -> None:
    """状态栏回执（控件缺失 → 静默）。"""
    try:
        panel._status_lbl.setText(str(text))
    except Exception:
        logger.debug("effect_browser_w2: set status failed")


def refresh_list(panel) -> None:
    """走唯一列表重建入口 ``_filter_list``。"""
    try:
        panel._filter_list()
    except Exception:
        logger.debug("effect_browser_w2: refresh list failed", exc_info=True)


def display_name(panel, preset_id: str) -> str:
    """预设显示名（取不到 → id 本身）。"""
    try:
        return str(panel._effect_display_name(preset_id))
    except Exception:
        return str(preset_id)


def search_text(panel) -> str:
    """搜索框当前文本（控件缺失 → ""）。"""
    try:
        return str(panel._search.text() or "")
    except Exception:
        return ""


def preset_switched(panel, preset_id: str) -> None:
    """切换/载入预设后的收口：#17 dirty 归零 + #6 A/B 槽跨预设作废 + 宏计数。

    由 ``_load_preset_params`` 每次落值后调用 —— dirty 的语义是"相对当前载入
    这套默认值的偏离"，载入即归零。
    """
    try:
        from .effect_browser_w2_dirty import mark_clean
        mark_clean(panel)
        _ab_invalidate(panel, preset_id)
        from .effect_browser_w2_macro import sync_macro_labels
        sync_macro_labels(panel)
    except Exception:
        logger.debug("effect_browser_w2: preset switched hook failed", exc_info=True)


def _ab_invalidate(panel, preset_id: str) -> None:
    """A/B 槽属于"哪个预设"的记忆：换预设即作废（跨预设对比会读到无关参数）。"""
    ab = getattr(panel, "_w2_ab", None)
    if not isinstance(ab, dict):
        return
    if str(ab.get("preset", "") or "") == str(preset_id):
        return
    ab.update({"slot": "", "A": None, "B": None, "preset": str(preset_id)})


def note_usage(preset_id) -> int:
    """#10：记一次使用（热度榜数据源）；返回累加后的次数。"""
    try:
        from . import effect_browser_w2_store as store
        return int(store.bump_usage(preset_id))
    except Exception:
        logger.debug("effect_browser_w2: note usage failed", exc_info=True)
        return 0


def heat_ids(limit: int = 0) -> List[str]:
    """#10：按使用次数降序的预设 id 表。"""
    try:
        from . import effect_browser_w2_store as store
        return list(store.heat_ids(limit))
    except Exception:
        return []


__all__ = [
    "NON_PARAM_KEYS", "NUMERIC_TYPES", "default_value_for", "display_name",
    "engine", "heat_ids", "note_usage", "num", "preset_id_of", "preset_of",
    "preset_switched", "refresh_list", "search_text", "set_status", "spec_map",
    "spec_spans", "spec_defaults", "spec_span", "widget_value",
]
