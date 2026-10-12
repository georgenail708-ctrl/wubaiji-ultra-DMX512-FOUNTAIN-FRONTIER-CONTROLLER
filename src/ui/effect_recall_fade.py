# -*- coding: utf-8 -*-
"""src/ui/effect_recall_fade.py — R183 批 A 快照召回过渡的**纯数学层**（无 Qt）。

用户价值：调度台/场景召回是"跳变"的 —— 参数瞬间切到新值，现场看到硬切。
本模块把参数在 N 秒内从**上一格正在输出**的值插值到目标值，缓动数学复用引擎既有
的 `EASE_FUNCS_V`（``src/engine/output_smoother.py``；Matrix 面板
``matrix_slideshow/output_shaper.py`` 已有同一处复用先例）—— 不新增缓动实现、
不新增依赖。

★ 0 秒 = 旧行为**逐字节一致**：`recall_fade_params` 在无过渡记录时返回调用方
  传进来的**同一个对象**（调用方随后 `dict(...)` 之，值一分不改），且过渡记录走完
  即从状态里弹出 → 后续每帧零额外开销、与"从未配置过渡"完全同路径。

语义边界（刻意保守）：
  · 数值键（int/float，不含 bool）按缓动比例混合；int 键保持 int（round）——
    因此进度 1 逐字节等于目标值、进度 0 逐字节等于起点值；
  · 非数值键（choice 字符串 / 布尔 / 设备 id 列表 / 参数结构）**立即取目标值**：
    形状类参数没有"中间态"，硬插会喂出非法值（旧行为在这类键上不变）；
  · dict / 等长 list 递归进（`_channel_forms` 内层的数值参数因此也会淡）；
  · 目标格此前**没有输出** → 调用方不建记录：没有可信起点，宁可不淡（不编造起点）。

参考: https://doc.qt.io/qt-6/qpropertyanimation.html（Qt 官方动画的时长/缓动交互
      语义：时长 0 = 立即到位；本模块把同一语义落到参数值上，不引 Qt 动画对象——
      播放帧由既有 25ms `_play_timer` 驱动，多一个动画对象只会多一份时序真源）
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

#: 过渡曲线键（取值域 = 引擎 ``EASE_FUNCS_V`` 的键；不新增缓动数学）
FADE_CURVE = "ease_in_out"
#: 过渡时长上限（秒）
FADE_MAX = 10.0
#: 过渡记录在 external state 里的存放键
FADE_STATE_KEY = "recall_fade"


def num_or(v, default: float) -> float:
    """数值容错（非数值 → default）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def ease_frac(curve, frac: float) -> float:
    """按缓动键求缓动后比例（键缺失/异常 → 线性；输出恒在 [0,1]）。"""
    f = max(0.0, min(1.0, num_or(frac, 0.0)))
    try:
        from ..engine.output_smoother import EASE_FUNCS_V
        fn = EASE_FUNCS_V.get(str(curve or "").strip().lower())
        if fn is None:
            return f
        return max(0.0, min(1.0, num_or(fn(f), f)))
    except Exception:
        _log.debug("recall_fade: ease curve lookup failed", exc_info=True)
        return f


def lerp_number(a, b, f: float):
    """数值插值：int↔int 保持 int（f=1 逐字节等于 b、f=0 逐字节等于 a）。"""
    if isinstance(a, int) and isinstance(b, int):
        return int(round(a + (b - a) * f))
    return float(a) + (float(b) - float(a)) * f


def same_len_list(a, b) -> bool:
    """两者都是等长 list/tuple（可逐项插值）。"""
    return (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
            and len(a) == len(b))


def lerp_param_value(a, b, f: float):
    """单个参数值插值：数值混合（int 保持 int）/dict 与等长 list 递归/其余取 b。

    刻意不给字符串做"逐字符插值"：choice 类参数没有中间态，硬插会喂出非法值。
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return lerp_number(a, b, f)
    if isinstance(a, dict) and isinstance(b, dict):
        return lerp_param_dict(a, b, f)
    if same_len_list(a, b):
        return [lerp_param_value(x, y, f) for x, y in zip(a, b)]
    return b


def lerp_param_dict(src, dst, f: float) -> dict:
    """两组参数 dict 的插值（dst 为键集合真源：缺键/类型不符一律取 dst）。"""
    out = {}
    for key, target in (dst or {}).items():
        if key in (src or {}):
            out[key] = lerp_param_value(src[key], target, f)
        else:
            out[key] = target
    return out


def fade_progress(fade, now: float):
    """过渡进度（0..1；记录非法/时长为 0 → None=不淡）。"""
    if not isinstance(fade, dict):
        return None
    dur = num_or(fade.get("dur"), 0.0)
    if dur <= 0.0:
        return None
    frac = (num_or(now, 0.0) - num_or(fade.get("t0"), now)) / dur
    if frac >= 1.0:
        return None
    return max(0.0, frac)


def make_fade_record(from_params, scalars, seconds, now: float, curve: str = FADE_CURVE):
    """构造过渡记录；秒数 ≤0 → None（调用方据 None 判定"一个字节都不写"）。"""
    secs = max(0.0, min(FADE_MAX, num_or(seconds, 0.0)))
    if secs <= 0.0:
        return None
    return {"from": dict(from_params or {}), "scalars": dict(scalars or {}),
            "dur": float(secs), "t0": float(num_or(now, 0.0)),
            "curve": str(curve or FADE_CURVE)}


def recall_fade_params(ext_state, target_params, now: float) -> dict:
    """按召回过渡进度返回本帧应使用的参数（无过渡 → 原样返回 target_params）。

    过渡走完（进度 ≥ 1）时**弹出**过渡记录并返回目标对象：之后每帧零额外开销，
    且与"从未配置过渡"的路径逐字节相同。
    """
    if not isinstance(ext_state, dict):
        return target_params
    frac = fade_progress(ext_state.get(FADE_STATE_KEY), now)
    if frac is None:
        ext_state.pop(FADE_STATE_KEY, None)
        return target_params
    fade = ext_state[FADE_STATE_KEY]
    eased = ease_frac(fade.get("curve"), frac)
    return lerp_param_dict(fade.get("from") or {}, target_params, eased)


def recall_fade_scalar(ext_state, key, target, now: float):
    """过渡记录里某个**顶层标量**（如播放倍率）的当前插值值。

    与 `recall_fade_params` 共用同一条记录（同一个 t0/dur/curve），因此参数与播放
    倍率同步到位；记录不存在/已走完/起点非数值 → 原样返回 target。起点存在
    `fade["scalars"]` 里（与参数起点 `fade["from"]` 分开放，避免两个 lerp 口径串味）。
    """
    if not isinstance(ext_state, dict):
        return target
    frac = fade_progress(ext_state.get(FADE_STATE_KEY), now)
    if frac is None:
        return target
    start = (ext_state[FADE_STATE_KEY].get("scalars") or {}).get(key)
    if isinstance(start, bool) or not isinstance(start, (int, float)):
        return target
    curve = ext_state[FADE_STATE_KEY].get("curve")
    return lerp_param_value(start, target, ease_frac(curve, frac))
