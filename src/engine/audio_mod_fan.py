# -*- coding: utf-8 -*-
"""多灯相位展开的运行时求值（R183 3.4b；消费 `audio_mod_spread` 的相位表）。

职责边界：
  · `audio_mod_spread` = 纯公式/序位（无状态、无规则知识）；
  · 本模块 = **展开的逐灯求值**（clip 路径逐灯精确值、规则路径逐桶标量）与
    逐地址 scale 数组（driver 的单域缩放直接消费）；
  · driver 只留粘合（计划构造 + 单域调用）——文件行数门禁纪律（compile/driver
    均在 500 行红线附近，展开逻辑集中在本模块）。

状态纪律：规则路径的 per-(规则, 桶) 滤波状态槽由 `_FAN_STRIDE`（见
`audio_mod_draw_dsp`）划分；`_rule_value` 的槽位 = 规则下标×stride + 桶号。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

#: 每桶状态槽与规则下标的步长（桶数上限对齐 `audio_mod_spread.MAX_FAN_BUCKETS`）
_FAN_STRIDE = 64

#: 桶查表 LUT 缓存（清查⑦A P2-13：键=(dict id, 项数, 长度) 三元防 id 复用；
#: 每帧 154 次查询从「每次重建 64 项 LUT」降为「身份命中直接索引」）
_BUCKET_LUT_CACHE: dict = {}


def plan(state: dict, project, by_uni, features, t: float, *,
         targets=()) -> Optional[dict]:
    """展开计划；未启用/无目标/单灯 → None（调用方走旧路径逐位等价）。

    产物：count（scope 内设备数）、buckets（每灯相位桶）、clipvals（clip 路径
    逐灯精确值，无 clips → None）、tick（规则路径 {通道类型: {桶: 标量}}）、
    arrs（{域号: 与 by_uni 位置对齐的序位数组}）。
    """
    try:
        from src.engine.audio_mod_spread import (address_ordinals, fan_buckets,
                                                 is_active, state_offsets)
        if not is_active(state):
            return None
        got = address_ordinals(project, state, by_uni)
        if not got:
            return None
        arrs, count = got
        offs = state_offsets(state, count)
        if not offs or count <= 1:
            return None
        buckets, _reps = fan_buckets(offs)
        return {"count": count, "buckets": buckets,
                "clipvals": (_clip_values_from_state(state, features, t, count)
                             if state.get("clips") else None),
                "tick": _rule_values(state, features, t, count, targets),
                "arrs": arrs}
    except Exception:
        logger.debug("spread fan plan failed", exc_info=True)
        return None


def view(fan: Optional[dict], universe: int) -> Optional[dict]:
    """单域展开视图（`scales` 消费）；该域无目标 → None。"""
    if not fan:
        return None
    arr = fan["arrs"].get(int(universe))
    if arr is None:
        return None
    return {"arr": arr, "buckets": fan["buckets"],
            "clipvals": fan["clipvals"], "tick": fan["tick"]}


def scales(scale: float, idx, ct_idx, fan: dict) -> "np.ndarray":
    """逐地址 scale 数组（位置对齐于 `idx`；未覆盖位置 = 全局 `scale`）。

    clip 路径：逐灯**精确值**（`fan["clipvals"]` 按序位直查）；
    规则路径：该 (通道类型, 相位桶) 标量**绝对覆盖**（照 `_overlay_ct_scales`
    口径——规则标量是绝对增益，不是乘子）。
    """
    arr = np.asarray(fan["arr"], dtype=np.int64)
    out = np.full(arr.size, float(scale), dtype=np.float64)
    clip = fan.get("clipvals")
    if clip is not None:
        pos = arr >= 0
        out[pos] = np.asarray(clip, dtype=np.float64)[arr[pos]]
    tick = fan.get("tick") or {}
    fan2 = dict(fan, _arr=arr, _out=out)      # 位置表复用（防重复 asarray）
    for ct, by_b in tick.items():
        pos = _ct_positions(idx, arr, (ct_idx or {}).get(str(ct)))
        if pos is None or pos.size == 0:
            continue
        bcol = np.asarray(fan2["buckets"], dtype=np.int64)
        vals = _lookup_bucket(by_b, bcol[arr[pos]])
        good = np.isfinite(vals)
        out[pos[good]] = vals[good]
    return out


def _rule_values(state: dict, features, t: float, count: int, targets):
    """规则卡展开下的逐灯标量 {通道类型: {桶: 值}}；不适用/异常 → {}。

    ★ 与 driver 旧标量入口互斥：`_frame_dt` 每帧只推进一次（per-rule 滤波
    状态的帧序列单点）。
    """
    if not state.get("rules") or int(count or 0) <= 1:
        return {}
    try:
        return _fan_accumulate(state, features, t, count, targets)
    except Exception:
        logger.debug("draw rule fan scale failed", exc_info=True)
        return {}


def _fan_accumulate(state: dict, features, t: float, count: int, targets):
    """`_rule_values` 正文（拆出压 CC）：env 门 → 桶表 → 逐桶叠加。"""
    from src.engine.audio_modulation import _active_envelope_norm
    from src.engine.audio_mod_draw_dsp import rules_ctx
    from src.engine.audio_mod_spread import fan_buckets, state_offsets
    env = (_active_envelope_norm(state, t) if state.get("clips") else 1.0)
    offs = state_offsets(state, count) if env > 0.0 else None
    wanted = {str(c) for c in (targets or ()) if str(c)}
    if not offs or not wanted:
        return {}
    _buckets, reps = fan_buckets(offs)
    amount = float(state.get("amount", 1.0) or 0.0)
    return accumulate_fanned(state["rules"], wanted, reps,
                             rules_ctx(state, features, t, env, amount))


def _ct_positions(idx, arr, addrs):
    """该通道类型的地址 → 在 `idx`（排序）内的位置（arr 有效位置）。

    ★ R183 清查①B5（P2 实锤）：searchsorted 后必须等值校验——规则目标 ⊄
      scope 目标时（target_ch_types 空+rules 类型∉回退集，旧档可达），夹逼
      命中的是排序**相邻**地址，scale 会打到错误通道。
    """
    if addrs is None or addrs.size == 0:
        return None
    pos = np.searchsorted(idx, addrs)
    pos = pos[pos < arr.size]
    if pos.size == 0:
        return None
    pos = pos[idx[pos] == addrs]          # 等值过滤（找不到的地址不缩放）
    if pos.size == 0:
        return None
    return pos[arr[pos] >= 0]


def _lookup_bucket(by_b: Dict, keys) -> "np.ndarray":
    """桶号数组 → 标量数组（缺桶 → NaN）。

    ★ 清查⑦A P2-13：LUT 按 by_b 身份缓存——真工程每帧 154 次调用（每域一次）
      且 by_b 每帧新建但逐域同一份，此前每次全量重建（64 项 Python 循环）。
      缓存键 = id + 项数（防 id 复用误命中的双保险）。
    """
    if keys.size == 0:
        return np.zeros(0, dtype=np.float64)
    want = int(keys.max()) + 1
    key = (id(by_b), len(by_b), want)
    lut = _BUCKET_LUT_CACHE.get(key)
    if lut is None:
        lut = np.full(want, np.nan, dtype=np.float64)
        for k, v in by_b.items():
            if 0 <= int(k) < lut.size:
                lut[int(k)] = float(v)
        if len(_BUCKET_LUT_CACHE) >= 32:
            _BUCKET_LUT_CACHE.clear()
        _BUCKET_LUT_CACHE[key] = lut
    return lut[keys]


def _clip_values_from_state(state: dict, features, t: float, count: int):
    """clip 路径逐灯调制值（精确；`clip_values` 的已归一化快路径）。"""
    return clip_values(state, features, t, count, state_is_normalized=True)


def clip_values(state: dict, features, t: float, count: int,
                state_is_normalized: bool = False) -> Optional[List[float]]:
    """clip 路径逐灯调制值（长度 `count`；序位 = 配接顺序）。

    未启用展开（三项全 0）/ 灯数 ≤1 → None（调用方走全局值 = 旧行为）。
    每灯 = clip 包络斜坡（时间项，整组共享）× 波形(t, 该灯相位偏移) ×
    特征 × amount 的 0..1 钳位——与 `modulation_value` 同口径，只是波形项
    按灯相位取值（MA3 相位 = "每灯时间偏移" 的波形等价物）。
    """
    from src.engine.audio_modulation import (_clip_envelope_term,
                                             _clip_wave_value, _clamp01,
                                             feature_value, normalize_state)
    s = state if state_is_normalized else normalize_state(state)
    offs = _offsets_of(s, count)
    if not offs or len(offs) <= 1:
        return None
    feat = feature_value(features, s.get("feature", "beat_strength"), t)
    amount = s["amount"]
    out = []
    for off in offs:
        out.append(_phase_value(s, off, feat, float(t), amount))
    return out


def _offsets_of(state: dict, count: int):
    """state + 灯数 → 相位偏移表（未启用/无灯 → None）。"""
    from src.engine.audio_mod_spread import state_offsets
    try:
        return state_offsets(state, int(count or 0))
    except (TypeError, ValueError):
        return None


def _phase_value(state: dict, offset: float, feat: float, t: float,
                 amount: float) -> float:
    """某相位偏移灯的调制值（`clip_values` 求值体；无活动 clip → 0.0）。"""
    from src.engine.audio_modulation import (_clip_envelope_term,
                                             _clip_wave_value, _clamp01)
    # ★ R183 清查②③#1（P1）：重叠区段与 B4 渲染统一**排后者胜**——修前首条
    #   命中即返回，开展开后每盏灯播的是 B4 已废弃的"首条胜"层级（探针实证
    #   全局单灯与逐灯输出分叉）。
    out = 0.0
    for c in state.get("clips") or []:
        if c["start"] <= t <= c["end"]:
            ramp = _clip_envelope_term(state, c, t)
            if ramp <= 0.0:
                out = 0.0
                continue
            out = _clamp01(ramp * _clip_wave_value(c, t, offset) * feat * amount)
    return out


def accumulate_fanned(rows, wanted: set, offsets, ctx: dict):
    """多灯展开的规则叠加：{通道类型: {桶号: scale}}（R183 3.4b）。

    概率合成口径与 `audio_mod_draw_compile._accumulate_rules` 一致（逐桶各自
    合成）；`offsets` = 每桶代表相位偏移（`audio_mod_spread.fan_buckets` 产物）。
    相位经 `_rule_value(..., phase_lift=)` 注入（不复制规则参数表）；状态槽 =
    规则下标×`FAN_STRIDE` + 桶号（每桶独立，互不覆盖）。
    """
    from src.engine.audio_mod_draw_compile import (_match_targets, _rule_value,
                                                   _target_lut)
    acc: Dict[str, Dict[int, float]] = {}
    lut = _target_lut(wanted)      # 清查⑦A P1-7：大小写不敏感（与标量路径同源）
    for idx, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        targets = [str(c) for c in (r.get("ch_types")
                                    or [r.get("ch_type", "")]) if str(c)]
        hits = _match_targets(targets, lut)
        if not hits:
            continue
        for b, off in enumerate(offsets):
            v = _rule_value(r, ctx, idx * _FAN_STRIDE + b, phase_lift=off)
            for ct in hits:
                d = acc.setdefault(ct, {})
                d[b] = 1.0 - (1.0 - d.get(b, 0.0)) * (1.0 - v)
    env = ctx["env"]
    return {ct: {b: min(1.0, max(0.0, env * v)) for b, v in d.items()}
            for ct, d in acc.items()}
