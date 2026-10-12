# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_ab.py — R183 W2-FX #6：实时 A/B 对比槽。

两套参数快照（A/B）一键互换，用于"调参对比不靠记忆"：
  · 参数读写完全复用既有路径（``_capture_current_param_values`` /
    ``_route_param_values`` → 与手工拖动滑杆等价），本模块不引入新的写通道；
  · 槽内容随工程状态持久化（``state_for_save`` / ``apply_saved_state``），
    无槽时不写键 → 旧工程存档字节不变；
  · 槽属于"哪个预设"记在 ``preset``：换预设即作废（跨预设对比会读到无关参数，
    由 ``effect_browser_w2_base.preset_switched`` 收口）。

参考: 本仓 ``dispatch_capture_state``（effect_browser:8304）已证明"参数字典快照
      + 按 id 召回"的模式可行；此处只是两槽互换，不引第三方对比组件（已搜无适用）。
"""
from __future__ import annotations

import logging
from typing import Optional

from . import effect_browser_w2_base as _base

logger = logging.getLogger(__name__)

#: 工程状态里的键名（save_state/load_state 用）
W2_STATE_KEY = "w2_fx"
#: 合法槽位
SLOTS = ("A", "B")


def _ab_state(panel) -> dict:
    """槽状态容器（挂在面板上；缺失/异形则重建）。"""
    ab = getattr(panel, "_w2_ab", None)
    if not isinstance(ab, dict):
        ab = {"slot": "", "A": None, "B": None, "preset": ""}
        panel._w2_ab = ab
    return ab


def _capture(panel):
    """抓当前参数快照（先落分组 UI 态，再取参数控件值）；失败 → None。"""
    try:
        panel._save_current_group_ui_state()
    except Exception:
        logger.debug("effect_browser_w2_ab: save group ui state failed")
    try:
        return dict(panel._capture_current_param_values() or {})
    except Exception:
        logger.debug("effect_browser_w2_ab: capture params failed", exc_info=True)
        return None


def ab_capture(panel, slot: str) -> bool:
    """把当前参数快照存入 A/B 槽；返回是否成功（槽位非法/取参失败 → False）。"""
    slot = str(slot or "").upper()
    if slot not in SLOTS:
        return False
    params = _capture(panel)
    if params is None:
        return False
    ab = _ab_state(panel)
    ab[slot] = params
    ab["preset"] = _base.preset_id_of(panel)
    if not ab.get("slot"):
        ab["slot"] = slot
    _base.set_status(panel, f"已存入 {slot} 槽")
    return True


def ab_swap(panel) -> str:
    """当前参数与另一槽互换；返回生效槽位（对槽为空时先把现况存进对槽）。

    首次按下（只有一个槽有值）→ 把当前参数存进空槽并**把槽位切到它**：
    界面参数此刻 == 该槽内容，所以槽位标记如实、卡片徽标与下一次互换的方向都对。
    第二次起才真正互换（写回本槽 → 载入对槽 → 切槽位）。
    """
    ab = _ab_state(panel)
    cur = str(ab.get("slot") or SLOTS[0])
    other = SLOTS[1] if cur == SLOTS[0] else SLOTS[0]
    if ab.get(other) is None:
        if not ab_capture(panel, other):
            return cur
        ab["slot"] = other
        _base.set_status(panel, f"已把当前参数存进 {other} 槽；再按一次互换")
        return other
    return _swap_now(panel, ab, cur, other)


def _swap_now(panel, ab: dict, cur: str, other: str) -> str:
    """互换主体：现况写回本槽 → 另一槽参数应用到界面 → 槽位切换。"""
    params = _capture(panel)
    if params is None:
        return cur
    ab[cur] = params
    try:
        panel._route_param_values(dict(ab.get(other) or {}))
    except Exception:
        logger.debug("effect_browser_w2_ab: apply other slot failed", exc_info=True)
        return cur
    ab["slot"] = other
    _base.set_status(panel, f"已切到 {other} 槽参数")
    return other


def ab_slot(panel) -> str:
    """当前生效的 A/B 槽位名（未入槽 → ""）。"""
    return str((getattr(panel, "_w2_ab", None) or {}).get("slot", "") or "")


def ab_summary(panel) -> str:
    """槽位摘要（"A/B 均已存" / "仅 A 槽" / "空"），供菜单/状态提示。"""
    ab = _ab_state(panel)
    have = [s for s in SLOTS if ab.get(s) is not None]
    if not have:
        return "空"
    return "、".join(f"{s} 槽" for s in have)


def state_for_save(panel) -> dict:
    """工程内快照（无内容 → {}，调用方据此不写键）。"""
    ab = getattr(panel, "_w2_ab", None) or {}
    if not (ab.get("A") or ab.get("B")):
        return {}
    return {"slot": str(ab.get("slot", "") or ""),
            "preset": str(ab.get("preset", "") or ""),
            "A": ab.get("A"), "B": ab.get("B")}


def apply_saved_state(panel, state) -> bool:
    """从工程状态恢复（缺键/异形 → 忽略，绝不抛）。"""
    if not isinstance(state, dict):
        return False
    if not (state.get("A") or state.get("B")):
        return False
    panel._w2_ab = {"slot": str(state.get("slot", "") or ""),
                    "preset": str(state.get("preset", "") or ""),
                    "A": state.get("A"), "B": state.get("B")}
    return True


def slot_for_preset(panel, preset_id) -> Optional[str]:
    """该预设当前生效的槽位（非当前选中/未入槽 → None）；卡片 A/B 徽标用。"""
    if str(preset_id) != _base.preset_id_of(panel):
        return None
    slot = ab_slot(panel)
    return slot if slot in SLOTS else None


__all__ = ["SLOTS", "W2_STATE_KEY", "ab_capture", "ab_slot", "ab_summary",
           "ab_swap", "apply_saved_state", "slot_for_preset", "state_for_save"]
