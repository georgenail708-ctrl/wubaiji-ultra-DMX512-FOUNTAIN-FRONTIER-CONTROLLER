# -*- coding: utf-8 -*-
"""trajectory_grid — 轨迹曲线画布的节拍吸附网格数学（R180，纯函数无 Qt）

节拍网格可视化 + 近网格吸附判据。网格语义与 TrajectoryClock 一致：

  - 吸附格间距 spacing = 拍长 × division（1/2/4 拍一格），是**吸附目标**；
  - 可视化在其上叠半格/四分格细分线（1/1、1/2、1/4 拍层级亮度递减），
    细分线是视觉参照、不是吸附目标；
  - 画布过小时主格自动 ×2 疏化（像素间距 < GRID_MIN_MAJOR_PX 加倍），
    细分阶梯按像素密度（≥ GRID_MIN_MINOR_PX）向下补齐——疏化只是把
    格线降级为更淡的细分线，吸附格线永远落在已画的某层级上（吸附
    目标永不落空，见 _grid_level 的 2 幂因子归层）。

近网格判据用**屏幕像素阈值**（SNAP_PX_THRESHOLD）：拖点离最近格线 ≤ 阈值
才预对齐/吸附，否则原样保留——所见即所得（画的格 = 会吸的格）。
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

#: 主格线（吸附格）最小像素间距；过密自动 ×2 疏化。
GRID_MIN_MAJOR_PX = 24.0
#: 细分线最小像素间距；更密则该级不画。
GRID_MIN_MINOR_PX = 9.0
#: 近网格吸附阈值（屏幕像素）：拖点 x 距最近格线 ≤ 此值才吸附。
SNAP_PX_THRESHOLD = 12.0
#: 疏化上限：主格最多放大 ×64（防极端缩放死循环）。
_MAX_COARSEN = 64.0


def effective_spacing(spacing: float, px_per_sec: float,
                      min_px: float = GRID_MIN_MAJOR_PX) -> float:
    """主格间距（秒）按像素密度疏化：spacing×2 直到像素间距 ≥ min_px。

    返回值恒为 spacing 的 2 幂倍，仍是吸附间距的整数倍 → 疏化后主格线
    仍是合法吸附位置（网格与吸附不会打架）。
    """
    s = float(spacing)
    limit = s * _MAX_COARSEN
    while s * px_per_sec < min_px and s < limit:
        s *= 2.0
    return s


def _subdivision_steps(major: float, px_per_sec: float,
                       min_minor_px: float) -> List[float]:
    """细分阶梯：主格 S/2、S/4…按**像素密度**向下补（≥ min_minor_px 才画）。

    停止条件只有像素下限与 8 级上限——主格未疏化（major==spacing）时
    同样要能展开半格/四分格（吸附格线由 k 的 2 幂因子保证仍落在已画
    层级上，见 _grid_level）。
    """
    steps = [major]
    while steps[-1] * 0.5 * px_per_sec >= min_minor_px and len(steps) < 8:
        steps.append(steps[-1] * 0.5)
    return steps


def _grid_level(k: int, n_levels: int) -> int:
    """k·fine 的层级：0=主格；k 含 2 幂因子越多，落在越淡的细分级。"""
    if k == 0:
        return 0
    tz = 0
    kk = k
    while kk % 2 == 0 and tz < n_levels - 1:
        tz += 1
        kk //= 2
    return (n_levels - 1) - tz


def beat_grid_lines(t0: float, t1: float, origin: float, spacing: float,
                    px_per_sec: float, *,
                    min_major_px: float = GRID_MIN_MAJOR_PX,
                    min_minor_px: float = GRID_MIN_MINOR_PX) -> List[Tuple[float, int]]:
    """曝光区 [t0,t1]（秒）内的节拍格线：返回 [(t, level)]。

    level 0 = 主格（吸附格，最亮），level≥1 = 逐级细分线（半格/四分格…，
    越大越淡）。细分阶梯 = S/2、S/4…（S=疏化后主格间距），按像素密度
    向下补（≥ min_minor_px 才画，≤8 级）；吸附格线必落在已画层级上。
    """
    spacing = float(spacing)
    px_per_sec = float(px_per_sec)
    if spacing <= 1e-9 or px_per_sec <= 1e-9 or t1 <= t0:
        return []
    major = effective_spacing(spacing, px_per_sec, min_major_px)
    steps = _subdivision_steps(major, px_per_sec, min_minor_px)
    fine = steps[-1]
    n_levels = len(steps)
    # k*fine 落在 level j（0=主格）当且仅当 k 被 2^(n-1-j) 整除。
    k0 = int(math.ceil((float(t0) - origin) / fine - 1e-9))
    k1 = int(math.floor((float(t1) - origin) / fine + 1e-9))
    return [(origin + k * fine, _grid_level(k, n_levels))
            for k in range(k0, k1 + 1)]


def nearest_grid_t(t: float, origin: float, spacing: float) -> float:
    """最近吸附格时刻（与 trajectory_clock.snap_to_grid 同式，四舍五入）。"""
    if spacing <= 1e-9:
        return float(t)
    return float(origin) + round((float(t) - origin) / spacing) * spacing


def near_grid_t(t: float, origin: float, spacing: float, px_per_sec: float,
                px_threshold: float = SNAP_PX_THRESHOLD) -> Optional[float]:
    """t 距最近格线 ≤ px_threshold 像素 → 返回吸附目标时刻；否则 None。"""
    if px_per_sec <= 1e-9:
        return None
    target = nearest_grid_t(t, origin, spacing)
    if abs(target - float(t)) * px_per_sec <= px_threshold:
        return target
    return None
