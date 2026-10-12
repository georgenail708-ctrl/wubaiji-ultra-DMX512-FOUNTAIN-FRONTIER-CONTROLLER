# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_random.py — R183 W2-FX #5 随机预设 + #15 骰子微扰。

两个**语义完全不同**的随机，必须分开（用户实报过这个歧义）：
  · #5 「随机预设」：换的是**效果本身** —— 从当前可见集里 `random.choice`
    一个预设并走既有 ``_on_preset_click``（与手动点卡片逐字节同路径）；
  · #15 「随机」按钮（底部既有钮）：换的是**这套效果的参数值** —— 默认改成
    "当前值 ±30% 区间微扰"（钳位到各自区间），现场调参不再"一下就面目全非"；
    关掉工具栏「微扰」后回到旧的全域随机（``_randomize_current_params``）。

为什么微扰值在模块里算、写回仍走 ``_route_param_values``：本仓的"回填一组参数"
只有这一条写通道（与手工拖动滑杆等价的信号链），不新开第二条。
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)

#: 微扰幅度（占参数全域的比例；±15% 行程 → 半幅 0.3/2）
PERTURB_FRAC = 0.3


def random_preset_id(panel) -> str:
    """从当前可见集随机取一个预设 id（空集/仅当前项 → ""）。"""
    current = _base.preset_id_of(panel)
    candidates = [pid for pid in sorted(getattr(panel, "_visible_preset_ids", set()) or ())
                  if pid and pid != current]
    return str(random.choice(candidates)) if candidates else ""


def random_preset(panel) -> str:
    """随机预设并载入（走既有 ``_on_preset_click``）；返回新选中 id。"""
    pid = random_preset_id(panel)
    if not pid:
        return ""
    try:
        panel._on_preset_click(pid)
    except Exception:
        logger.debug("effect_browser_w2_random: preset click failed", exc_info=True)
        return ""
    _base.set_status(panel, f"随机预设：{_base.display_name(panel, pid)}")
    return pid


def perturb_values(spans, current, frac: float = PERTURB_FRAC) -> Dict[str, float]:
    """纯函数：``{key: (lo, hi)}`` + ``{key: 当前值}`` → ±frac 行程微扰（钳位）。"""
    out: Dict[str, float] = {}
    for key, span in (spans or {}).items():
        value = _perturb_one(span, (current or {}).get(key), frac)
        if value is not None:
            out[str(key)] = value
    return out


def _perturb_one(span, cur, frac: float):
    """单参数微扰结果（非法输入 → None）。"""
    lo = _base.num(span[0]) if span else None
    hi = _base.num(span[1]) if span else None
    value = _base.num(cur)
    if lo is None or hi is None or value is None or hi <= lo:
        return None
    half = float(frac) * (hi - lo) / 2.0
    return max(lo, min(hi, value + random.uniform(-half, half)))


def perturb_current(panel) -> Dict[str, float]:
    """对当前预设的数值参数做微扰写回；返回实际写入表。"""
    spans = _base.spec_spans(panel)
    current = {key: _base.widget_value(panel, key) for key in spans}
    values = perturb_values(spans, current)
    if not values:
        return {}
    try:
        panel._route_param_values(values)
    except Exception:
        logger.debug("effect_browser_w2_random: route perturb failed", exc_info=True)
        return {}
    return values


def randomize_or_perturb(panel) -> str:
    """底部「随机」按钮路由：``"perturb"``（微扰）或 ``"full"``（旧全域随机）。"""
    if _store.pref("dice_perturb", True):
        perturb_current(panel)
        return "perturb"
    try:
        panel._randomize_current_params()
    except Exception:
        logger.debug("effect_browser_w2_random: full randomize failed", exc_info=True)
        return ""
    return "full"


def set_perturb_mode(panel, on: bool) -> None:
    """切「微扰 ↔ 全域随机」并记住偏好。"""
    _store.set_pref("dice_perturb", bool(on))


__all__ = ["PERTURB_FRAC", "perturb_current", "perturb_values", "random_preset",
           "random_preset_id", "randomize_or_perturb", "set_perturb_mode"]
_ = Any
