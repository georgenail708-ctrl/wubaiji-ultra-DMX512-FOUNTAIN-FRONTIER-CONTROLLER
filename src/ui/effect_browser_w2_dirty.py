# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_dirty.py — R183 W2-FX #17：改动记账 + 「回到默认」。

问题：预设参数被拖乱后，用户不知道动了哪几个，"重置全部"又会把没动过的参数
一起打回默认（观感上像"我明明只调了一个"）。

本模块：
  · 记 **dirty 参数键**（数据源 = 主面板的控件轨迹汇口 ``controlTrajectoryEvent``
    槽，见 ``note_trajectory``；载入预设即归零，见 ``base.preset_switched``）；
  · 标题行显「已改 N 项」（``update_dirty_hint``，标签由 ``build_dirty_label`` 建）；
  · **只**把 dirty 参数恢复预设默认（``reset_dirty``），未改动的一个不碰。

为什么不放进 ``_on_param_changed`` 函数体：该方法行数/CC 已在门禁基线内零余量
（33 行 CC 15，基线同值）—— 任何新增行都是"债务恶化"ERROR。控件轨迹信号是
同一汇口（每个参数控件都经 ``_on_param_changed`` 发一次），在此旁路监听等价。
参考: 本仓 diff 先例 ``studio_session.py:82 diff_fields``（只列差异字段的思路）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import effect_browser_w2_base as _base

logger = logging.getLogger(__name__)

#: 顶层主题前缀（控件轨迹里的参数主题）
TOPIC_PREFIX = "effect|param|"


def note_trajectory(panel, topic, value) -> None:
    """控件轨迹汇口旁路：把 ``effect|param|<key>`` 记成 dirty（其它主题忽略）。"""
    try:
        text = str(topic or "")
        if text.startswith(TOPIC_PREFIX):
            mark_dirty(panel, text[len(TOPIC_PREFIX):])
    except Exception:
        logger.debug("effect_browser_w2_dirty: trajectory note failed", exc_info=True)


def mark_dirty(panel, key: str) -> int:
    """记一个"被改动的参数"；返回当前 dirty 数（结构键不算，见 base.NON_PARAM_KEYS）。"""
    key = str(key or "")
    if not key or key in _base.NON_PARAM_KEYS:
        return dirty_count(panel)
    dirty = getattr(panel, "_w2_dirty", None)
    if not isinstance(dirty, set):
        dirty = set()
        panel._w2_dirty = dirty
    dirty.add(key)
    update_dirty_hint(panel)
    return len(dirty)


def mark_clean(panel, key: str = "") -> int:
    """清 dirty（``key`` 为空 → 全清）；返回剩余数。"""
    if key:
        dirty = getattr(panel, "_w2_dirty", None)
        if isinstance(dirty, set):
            dirty.discard(str(key))
    else:
        panel._w2_dirty = set()
    update_dirty_hint(panel)
    return dirty_count(panel)


def dirty_keys(panel) -> List[str]:
    """当前 dirty 参数键（排序稳定 → 定测可断言）。"""
    return sorted(str(k) for k in (getattr(panel, "_w2_dirty", None) or ()))


def dirty_count(panel) -> int:
    return len(getattr(panel, "_w2_dirty", None) or ())


def dirty_hint(panel) -> str:
    """标题行提示文本（无改动 → ""）。"""
    n = dirty_count(panel)
    return f"已改 {n} 项" if n > 0 else ""


def update_dirty_hint(panel) -> None:
    """刷新标题行标签（控件缺失 → 静默）。"""
    lbl = getattr(panel, "_w2_dirty_lbl", None)
    if lbl is None:
        return
    text = dirty_hint(panel)
    try:
        lbl.setText(text)
        lbl.setVisible(bool(text))
    except Exception:
        logger.debug("effect_browser_w2_dirty: update hint failed")


def reset_values(specs, keys) -> Dict[str, Any]:
    """纯函数：``{key: 默认值}``（结构键/未知键跳过 → 不产生非法值）。"""
    out: Dict[str, Any] = {}
    for key in keys:
        spec = specs.get(str(key))
        if spec is None:
            continue
        value = _base.default_value_for(spec)
        if value is not None:
            out[str(key)] = value
    return out


def reset_dirty(panel) -> int:
    """「回到默认」：只把 dirty 参数恢复预设默认；返回复位键数。"""
    keys = dirty_keys(panel)
    if not keys:
        _base.set_status(panel, "没有改动过的参数")
        return 0
    values = reset_values(_base.spec_map(panel), keys)
    try:
        if values:
            panel._route_param_values(values)
    except Exception:
        logger.debug("effect_browser_w2_dirty: route reset values failed",
                     exc_info=True)
    mark_clean(panel)
    _base.set_status(panel, f"已复原 {len(values)} 个改动参数")
    return len(values)


__all__ = ["TOPIC_PREFIX", "dirty_count", "dirty_hint", "dirty_keys",
           "mark_clean", "mark_dirty", "note_trajectory", "reset_dirty",
           "reset_values", "update_dirty_hint"]
