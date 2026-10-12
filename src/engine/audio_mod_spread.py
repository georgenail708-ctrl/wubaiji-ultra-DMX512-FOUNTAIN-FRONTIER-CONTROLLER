# -*- coding: utf-8 -*-
"""多灯相位展开（Spread / Phase From-To）—— 纯逻辑无 Qt（R183 3.4b）。

语义出处（官方手册实抓 + 本仓沉淀，清单见 ai-memory/_references/spread-adoption.md）：
  · Avolites Titan Shape Generator：Spread 把同一形状按灯展开——手册标注
    `Spread = 12 (Phase = 30 degrees)` / `Spread = 6 (60°)` / `Spread = 2 (180°)`
    → 每灯相位步进 = 360/Spread 度；Offset = 形状起始相位；Beats 有
    `Match to Spread`；Overlay = 形状相对已编程值（本仓「按当前值缩放」同义）。
    https://manual.avolites.com/docs/effects/shape-generator
  · 主流控台 Phaser：Phase = "the timing offset of each fixture's attributes
    within a phaser"（按度显示）；Width/Transition/Measure/Speed 同族。
    https://help.malighting.com/主流控台/2.2/HTML/phaser.html
  · Phase From/To（首灯→末灯相位差）继承本仓沉淀 dsp-envelope-formulas §6
    （MA3 原文逐字定义本轮未抓到，诚实登记见 adoption.md §一）。

本仓公式（state 字段 `spread_deg` / `phase_from_deg` / `phase_to_deg`，单位度）：
    offset_i = (from + (to − from)·i/(count−1) + spread·i/count) mod 360 → /360
返回值 = **波形周期相位偏移** 0..1（0=同相；spread=360 且 from=to=0 时按灯铺满
一圈，首尾环绕）。三项全 0 → `phase_params` 返回 None = **旧行为短路判据**：
调用方据此走旧路径，输出逐位不变（铁律）。

序位定义：照 MA3/Avolites = **配接顺序**（universe、start_channel 升序；同址并列
按工程列表序），**scope 内计数**（有目标地址命中的设备才占相位槽）。
"""
from __future__ import annotations

import weakref
from typing import Dict, List, Optional, Tuple

import numpy as np

#: 相位全量（度；spread 铺满一圈的口径）
FULL_TURN_DEG = 360.0

#: 配接行表缓存（工程弱引用 + patch_rev + 设备数失效；照 driver A10 P0 纪律）
_FIX_CACHE: dict = {"ref": None, "rev": None, "n": None, "rows": None}
#: scope 序位缓存（by_uni 身份 + 行表身份 + patch_rev；by_uni 由 driver 按
#: （地址映射表身份, targets）记忆化 → 身份命中即同 scope 命中）
_ORD_CACHE: dict = {"by_uni": None, "rows": None, "rev": None,
                    "arrs": None, "count": 0}


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def clamp_deg(v) -> float:
    """相位参数归一化：0..360 度（缺失/非法 → 0.0）。"""
    return min(FULL_TURN_DEG, max(0.0, _f(v, 0.0)))


def fixture_phase_offset(index, count, spread_deg=0.0, from_deg=0.0,
                         to_deg=0.0) -> float:
    """第 index 台设备（共 count 台）的相位偏移，返回周期相位 0..1。

    公式（模块 docstring）：from + (to−from)·i/(count−1) + spread·i/count，
    取模 360 后 /360。count≤1 无展开项（取 from）。
    """
    n = max(1, _to_int(count, 1))
    i = min(max(0, _to_int(index, 0)), n - 1)
    a, b = clamp_deg(from_deg), clamp_deg(to_deg)
    frac_to = (i / (n - 1)) if n > 1 else 0.0
    deg = a + (b - a) * frac_to + clamp_deg(spread_deg) * (i / n)
    return (deg % FULL_TURN_DEG) / FULL_TURN_DEG


def phase_params(state) -> Optional[Tuple[float, float, float]]:
    """state → (spread_deg, from_deg, to_deg)；三项全 0 → None（=旧行为）。

    None 是**逐位等价短路判据**：调用方据此走旧路径，不构造展开表。
    """
    s = state if isinstance(state, dict) else {}
    spread = clamp_deg(s.get("spread_deg"))
    a = clamp_deg(s.get("phase_from_deg"))
    b = clamp_deg(s.get("phase_to_deg"))
    if spread == 0.0 and a == 0.0 and b == 0.0:
        return None
    return (spread, a, b)


def is_active(state) -> bool:
    """是否启用多灯展开（driver 恒等快速路径判据；零开销）。"""
    return phase_params(state) is not None


#: 偏移表缓存（清查⑦A P2-13：真工程 count=1356 单次 0.84ms、每帧调 2 次 →
#  按 (三项度, count) 记忆化；调用方可能改列表 → 命中返回浅拷贝）
_OFFSETS_CACHE: dict = {}


def state_offsets(state, count: int) -> Optional[List[float]]:
    """state → 每灯相位偏移表（长度 count）；未启用/无灯 → None（旧行为）。"""
    p = phase_params(state)
    n = _to_int(count, 0)
    if p is None or n <= 0:
        return None
    key = (p[0], p[1], p[2], n)
    cached = _OFFSETS_CACHE.get(key)
    if cached is not None:
        return list(cached)
    offs = [fixture_phase_offset(i, n, p[0], p[1], p[2]) for i in range(n)]
    if len(_OFFSETS_CACHE) >= 16:
        _OFFSETS_CACHE.clear()
    _OFFSETS_CACHE[key] = tuple(offs)
    return offs


#: 规则路径扇出桶数上限（相位量化边界；clip 路径不量化仍逐灯精确）
MAX_FAN_BUCKETS = 64


def fan_buckets(offsets, cap: int = MAX_FAN_BUCKETS) -> Tuple[List[int], List[float]]:
    """相位偏移表 → (每灯桶号表, 桶代表偏移表)。

    n ≤ cap：桶号 = 灯序位、代表值 = 该灯**精确**偏移（无损；count 1/2/10 的
    数值表测试走此路径）；n > cap：按 1/cap 量化（桶号 = round(off×cap)%cap，
    代表值 = 桶中心）——只用于规则路径的性能边界（clip 路径逐灯精确）。
    """
    offs = [float(o) for o in (offsets or [])]
    n = len(offs)
    if n <= 0:
        return [], []
    c = max(1, _to_int(cap, MAX_FAN_BUCKETS))
    if n <= c:
        return list(range(n)), offs
    buckets = [int(round(o * c)) % c for o in offs]
    return buckets, [k / c for k in range(c)]


# ── 地址 → 序位（配接顺序；scope 内计数）──────────────────────────────────

def _fixture_rows(project) -> List[tuple]:
    """工程 → 配接顺序行表 [(universe, start_channel, 列表序, [地址...])]（升序）。

    「地址」= (universe, addr0)，addr0 = start_channel−1+i（与 driver
    `_fixture_channel_addrs` 同口径；通道项非空即计址，对齐 `_ch_type_addr_map`）。
    """
    defs = {str(getattr(d, "id", "") or ""): d
            for d in (getattr(project, "fixture_defs", []) or [])}
    rows = []
    for i, fx in enumerate(getattr(project, "fixtures", []) or []):
        rows.append(_fixture_row(fx, defs, i))
    rows = [r for r in rows if r is not None]
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows


def _fixture_row(fx, defs, order: int) -> Optional[tuple]:
    """单台设备 → 行表行（无定义 → None；拆出压 CC）。"""
    d = defs.get(str(getattr(fx, "def_id", "") or ""))
    if d is None:
        return None
    uni = _to_int(getattr(fx, "universe", 1), 1)
    start = _to_int(getattr(fx, "start_channel", 1), 1)
    addrs = [(uni, start - 1 + j)
             for j, ch in enumerate(getattr(d, "channels", []) or [])
             if str(ch or "").strip()]
    return (uni, start, order, addrs)


def fixture_rows(project) -> List[tuple]:
    """`_fixture_rows` 的缓存版（工程弱引用 + patch_rev + 设备数复核）。"""
    fixtures = list(getattr(project, "fixtures", []) or [])
    rev = _to_int(getattr(project, "patch_rev", 0), 0)
    ref = _FIX_CACHE.get("ref")
    if (isinstance(ref, weakref.ref) and ref() is project
            and _FIX_CACHE.get("rev") == rev
            and _FIX_CACHE.get("n") == len(fixtures)):
        return _FIX_CACHE["rows"]
    rows = _fixture_rows(project)
    _FIX_CACHE.update(ref=weakref.ref(project), rev=rev,
                      n=len(fixtures), rows=rows)
    return rows


def _hit_fixture_ords(rows, scope: set) -> Dict[tuple, int]:
    """scope 命中的设备 → {地址: 序位}（序位按配接顺序；同址首占者胜）。"""
    ords: Dict[tuple, int] = {}
    k = 0
    for r in rows:
        if not any(a in scope for a in r[3]):
            continue
        for a in r[3]:
            if a in scope:
                ords.setdefault(a, k)
        k += 1
    return ords


def _ordinal_arrays(rows, by_uni) -> Tuple[Dict[int, "np.ndarray"], int]:
    """→ ({域号: 每位置设备序位(-1=未命中)}, scope 内设备数)。"""
    scope = {(int(u), int(a)) for u, arr in by_uni.items()
             for a in np.asarray(arr).tolist()}
    ords = _hit_fixture_ords(rows, scope)
    out: Dict[int, "np.ndarray"] = {}
    for u, arr in by_uni.items():
        base = np.asarray(arr, dtype=np.int64)
        col = np.full(base.size, -1, dtype=np.int64)
        for p, a in enumerate(base.tolist()):
            k = ords.get((int(u), int(a)))
            if k is not None:
                col[p] = k
        out[int(u)] = col
    return out, len({v for v in ords.values()})


def address_ordinals(project, state, by_uni):
    """目标地址 → 设备序位表：({域号: 数组(-1=未命中)}, scope 内设备数)。

    `by_uni` = driver `_scope_index` 的 {域号: 排序地址数组}（本函数返回的
    数组与其**位置对齐**，可直接 numpy 花式索引）。未启用 spread / 无目标 →
    None（调用方走旧路径）。
    """
    if phase_params(state) is None or not by_uni:
        return None
    rows = fixture_rows(project)
    rev = _to_int(getattr(project, "patch_rev", 0), 0)
    if (_ORD_CACHE.get("by_uni") is by_uni and _ORD_CACHE.get("rows") is rows
            and _ORD_CACHE.get("rev") == rev):
        return _ORD_CACHE["arrs"], _ORD_CACHE["count"]
    arrs, count = _ordinal_arrays(rows, by_uni)
    _ORD_CACHE.update(by_uni=by_uni, rows=rows, rev=rev, arrs=arrs, count=count)
    return arrs, count


def invalidate_caches() -> None:
    """缓存显式失效（换工程/调试/测试；driver.invalidate_caches 挂钩）。"""
    _FIX_CACHE.update(ref=None, rev=None, n=None, rows=None)
    _ORD_CACHE.update(by_uni=None, rows=None, rev=None, arrs=None, count=0)
    _OFFSETS_CACHE.clear()
