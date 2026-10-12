# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_macro.py — R183 W2-FX #8：参数宏（1 旋钮映射 N 参数）。

定义：``{宏键: {参数键: 行程倍数}}``（持久化在用户档，见 ``w2_store``）。
一个宏旋钮从中性位（预设默认值）左右移动时，**同时**推动所有被映射的参数：
    值 = 默认 + (t - 0.5) * scale * (hi - lo)，各自钳位到区间。

写回走既有参数写入路径（``_route_param_values``）→ 每个参数照常经过控件轨迹
汇口与既有信号链，宏不构成第二条写通道；未建立映射时**一个参数也不动**。

为什么不用现成方案：已搜无 Python/Qt 现成的"宏映射"组件（宿主宏是内建能力）；
本仓 ``_route_param_values`` 已经把"回填一组参数值"做完了，宏只需生成值。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)

#: 宏旋钮的中性位（滑杆 50/100；此处即预设默认值）
NEUTRAL = 0.5


def macro_values_for(spans, defaults, mapping, t: float,
                     neutral: float = NEUTRAL) -> Dict[str, float]:
    """纯函数：宏位置 ``t`` → {参数: 新值}（未知键/区间退化一律跳过）。"""
    out: Dict[str, float] = {}
    frac = float(t) - float(neutral)
    for key, scale in (mapping or {}).items():
        value = _macro_value(spans, defaults, key, scale, frac)
        if value is not None:
            out[str(key)] = value
    return out


def _macro_value(spans, defaults, key, scale, frac) -> Any:
    """单参数折算（缺区间/缺默认值 → None）。"""
    span = (spans or {}).get(str(key))
    base = (defaults or {}).get(str(key))
    if not span or base is None:
        return None
    lo = _base.num(span[0])
    hi = _base.num(span[1])
    ratio = _base.num(scale)
    if lo is None or hi is None or ratio is None or hi <= lo:
        return None
    return max(lo, min(hi, float(base) + frac * ratio * (hi - lo)))


def apply_macro(panel, macro_key: str, t: float) -> Dict[str, float]:
    """把宏位置写回其映射参数（重入保护：写回触发的信号不再递归展开宏）。"""
    if getattr(panel, "_w2_macro_busy", False):
        return {}
    panel._w2_macro_busy = True
    try:
        mapping = _mapping_of(panel, macro_key)
        if not mapping:
            return {}
        values = macro_values_for(_base.spec_spans(panel),
                                  _base.spec_defaults(panel), mapping, float(t))
        if values:
            panel._route_param_values(values)
        return values
    except Exception:
        logger.debug("effect_browser_w2_macro: apply failed", exc_info=True)
        return {}
    finally:
        panel._w2_macro_busy = False


def _mapping_of(panel, macro_key: str) -> dict:
    """该预设下某宏槽的映射表（无 → 空表）。"""
    preset_id = _base.preset_id_of(panel)
    return dict((_store.macro_def(preset_id).get(str(macro_key)) or {}))


def sync_macro_labels(panel) -> None:
    """刷新宏旋钮标签「M1(2)」= 已映射参数数（切换预设后调用）。"""
    labels = getattr(panel, "_w2_macro_labels", None) or {}
    preset_id = _base.preset_id_of(panel)
    macros = _store.macro_def(preset_id)
    for mkey, lbl in labels.items():
        _set_label(lbl, str(mkey), len(macros.get(str(mkey)) or {}))


def _set_label(lbl, mkey: str, count: int) -> None:
    try:
        lbl.setText(f"{mkey.upper()}({count})")
        lbl.setToolTip("未建立映射：在参数上右键「分配到宏…」" if count == 0
                       else f"已映射 {count} 个参数")
    except Exception:
        logger.debug("effect_browser_w2_macro: set label failed")


def mapping_for(panel, macro_key: str) -> dict:
    """对外只读视图（定测/菜单用）。"""
    return _mapping_of(panel, macro_key)


def assign_macro(panel, param_key: str, macro_key: str, scale: float = 1.0) -> bool:
    """把参数写进某宏槽（纯逻辑入口；对话框封装见 ``assign_macro_dialog``）。"""
    preset_id = _base.preset_id_of(panel)
    mkey = str(macro_key or "").strip().lower()
    param = str(param_key or "")
    if not preset_id or not param:
        return False
    mapping = dict(_store.macro_def(preset_id).get(mkey) or {})
    mapping[param] = float(scale)
    done = bool(_store.set_macro(preset_id, mkey, mapping))
    sync_macro_labels(panel)
    return done


def clear_macro(panel, param_key: str) -> int:
    """把参数从所有宏槽移除；返回移除的槽数。"""
    preset_id = _base.preset_id_of(panel)
    param = str(param_key or "")
    removed = 0
    for mkey in _store.MACRO_KEYS:
        mapping = _mapping_of(panel, mkey)
        if param not in mapping:
            continue
        mapping.pop(param, None)
        _store.set_macro(preset_id, mkey, mapping)
        removed += 1
    sync_macro_labels(panel)
    return removed


__all__ = ["NEUTRAL", "apply_macro", "assign_macro", "clear_macro",
           "macro_values_for", "mapping_for", "sync_macro_labels"]
