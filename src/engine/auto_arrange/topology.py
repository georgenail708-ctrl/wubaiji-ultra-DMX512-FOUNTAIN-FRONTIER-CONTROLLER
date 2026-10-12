"""
topology — 从设备真实坐标生成"拓扑子选择"的有序 member_ids。

纯几何逻辑层，仅依赖 math（不依赖 numpy / PyQt / 项目读取）。
供 direction / phase_spread 沿序铺波使用。

坐标真源约定（已踩坑）：
  topview_state.pages[].fixtures[fid] = {x, y}；同一设备在不同页坐标不同，
  (0, 0) 是"未布在此页"占位。本模块**不**自己读项目：调用方负责把每个
  member 解析成它在某一页上的首个非零坐标，再以 {fid: (x, y)} 形式传入。

几何原语照搬 src/ui/topview_panel.py 的写法，保证与 TopView 行为一致：
  _fit_circle (line 270) / _angular_span_deg (line 297) / _pca_axis (line 309)。
方向变换语义对齐 src/ui/effect_engine.py:apply_effect_direction (line 409)。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Coord = Tuple[float, float]
CoordMap = Dict[object, Coord]


# --------------------------------------------------------------------------- #
# 内部小工具
# --------------------------------------------------------------------------- #
def _coords_of(member_ids: Sequence[object], coords: CoordMap) -> List[Coord]:
    """按 member_ids 顺序取坐标；缺失的成员跳过。"""
    out: List[Coord] = []
    for fid in member_ids:
        pt = coords.get(fid)
        if pt is None:
            continue
        out.append((float(pt[0]), float(pt[1])))
    return out


def _present(member_ids: Sequence[object], coords: CoordMap) -> List[object]:
    """过滤出有坐标的 member_ids（保持原顺序）。"""
    return [fid for fid in member_ids if coords.get(fid) is not None]


def _solve_3x3(a: List[List[float]], b: List[float]) -> Optional[Tuple[float, float, float]]:
    """高斯消元解 3x3 线性方程组（照搬 topview_panel 思路）。"""
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) <= 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        pivot_val = m[col][col]
        for j in range(col, 4):
            m[col][j] /= pivot_val
        for r in range(3):
            if r == col:
                continue
            factor = m[r][col]
            if abs(factor) <= 1e-12:
                continue
            for j in range(col, 4):
                m[r][j] -= factor * m[col][j]
    return m[0][3], m[1][3], m[2][3]


# --------------------------------------------------------------------------- #
# 公共几何原语
# --------------------------------------------------------------------------- #
def fit_circle(pts: Sequence[Coord]) -> Optional[Tuple[float, float, float, float]]:
    """代数最小二乘圆拟合，返回 (cx, cy, r, rms)；点不足或退化返回 None。

    参考 src/ui/topview_panel.py:270 _fit_circle。
    """
    points = [(float(x), float(y)) for x, y in pts]
    if len(points) < 3:
        return None
    sx = sy = sx2 = sy2 = sxy = sx3 = sy3 = sx2y = sxy2 = 0.0
    for x, y in points:
        x2, y2 = x * x, y * y
        sx += x; sy += y; sx2 += x2; sy2 += y2; sxy += x * y
        sx3 += x2 * x; sy3 += y2 * y; sx2y += x2 * y; sxy2 += x * y2
    n = float(len(points))
    sol = _solve_3x3(
        [[sx2, sxy, sx], [sxy, sy2, sy], [sx, sy, n]],
        [-(sx3 + sxy2), -(sx2y + sy3), -(sx2 + sy2)],
    )
    if sol is None:
        return None
    d, e, f = sol
    cx, cy = -d / 2.0, -e / 2.0
    r2 = cx * cx + cy * cy - f
    if r2 <= 1.0:
        return None
    radius = math.sqrt(r2)
    errors = [abs(math.hypot(x - cx, y - cy) - radius) for x, y in points]
    rms = math.sqrt(sum(err * err for err in errors) / max(1, len(errors)))
    return cx, cy, radius, rms


def angular_span_deg(pts: Sequence[Coord], cx: float, cy: float) -> float:
    """最大间隙法算角覆盖度（度）。span>=300 可视为整环，否则为弧。

    参考 src/ui/topview_panel.py:297 _angular_span_deg。
    """
    points = [(float(x), float(y)) for x, y in pts]
    if len(points) < 2:
        return 0.0
    angles = sorted(math.atan2(y - cy, x - cx) for x, y in points)
    gaps: List[float] = []
    for a, b in zip(angles, angles[1:]):
        gaps.append(b - a)
    gaps.append((angles[0] + math.tau) - angles[-1])
    largest_gap = max(gaps) if gaps else math.tau
    return math.degrees(max(0.0, math.tau - largest_gap))


def pca_axis(pts: Sequence[Coord]) -> Tuple[float, float, float]:
    """PCA 主轴 (vx, vy, ratio)，ratio=minor/major（越小越线状）。

    参考 src/ui/topview_panel.py:309 _pca_axis。
    """
    points = [(float(x), float(y)) for x, y in pts]
    if len(points) < 2:
        return 1.0, 0.0, 0.0
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    sxx = sum((x - cx) * (x - cx) for x, _ in points) / len(points)
    syy = sum((y - cy) * (y - cy) for _, y in points) / len(points)
    sxy = sum((x - cx) * (y - cy) for x, y in points) / len(points)
    if abs(sxy) <= 1e-9 and abs(sxx - syy) <= 1e-9:
        return 1.0, 0.0, 1.0
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    vx, vy = math.cos(angle), math.sin(angle)
    major = (sxx + syy) / 2.0 + math.sqrt(((sxx - syy) / 2.0) ** 2 + sxy * sxy)
    minor = (sxx + syy) / 2.0 - math.sqrt(((sxx - syy) / 2.0) ** 2 + sxy * sxy)
    ratio = 0.0 if major <= 1e-9 else max(0.0, minor) / major
    if vx < 0.0 or (abs(vx) < 1e-9 and vy < 0.0):
        vx, vy = -vx, -vy
    return vx, vy, ratio


# --------------------------------------------------------------------------- #
# 排序器
# --------------------------------------------------------------------------- #
def order_by_angle(member_ids: Sequence[object], coords: CoordMap,
                   cx: float, cy: float) -> List[object]:
    """按 atan2(y-cy, x-cx) 升序排序（环上有序）。"""
    present = _present(member_ids, coords)

    def angle_of(fid: object) -> float:
        x, y = coords[fid]
        return math.atan2(float(y) - cy, float(x) - cx)

    return sorted(present, key=angle_of)


def order_by_axis(member_ids: Sequence[object], coords: CoordMap) -> List[object]:
    """PCA 主轴投影排序（线 / 排方向上有序）。"""
    present = _present(member_ids, coords)
    pts = [coords[fid] for fid in present]
    if len(pts) < 2:
        return present
    vx, vy, _ratio = pca_axis(pts)
    cx = sum(float(p[0]) for p in pts) / len(pts)
    cy = sum(float(p[1]) for p in pts) / len(pts)

    def proj(fid: object) -> float:
        x, y = coords[fid]
        return (float(x) - cx) * vx + (float(y) - cy) * vy

    return sorted(present, key=proj)


# --------------------------------------------------------------------------- #
# 方向变换（对齐 effect_engine.apply_effect_direction）
# --------------------------------------------------------------------------- #
def direction_order(ordered: Sequence[object], direction: str,
                    coords: Optional[CoordMap] = None) -> List[object]:
    """在已有序队列之上应用方向变换。

    'L2R'   按 x 升序     (需 coords；缺则保持原序)
    'R2L'   按 x 降序     (需 coords；缺则反转原序)
    'center_out' 从队列中心向两端展开（基于序号，无需坐标）
    'edge_in'    从两端向中心收拢
    其它/default 原样返回。
    """
    direction = str(direction or "default")
    items = list(ordered or [])
    if len(items) <= 1 or direction == "default":
        return items

    if direction in {"L2R", "left_to_right"}:
        if coords is None:
            return items
        return sorted(items, key=lambda f: float(coords.get(f, (0.0, 0.0))[0]))
    if direction in {"R2L", "right_to_left", "reverse"}:
        if coords is None:
            return list(reversed(items))
        return sorted(items, key=lambda f: float(coords.get(f, (0.0, 0.0))[0]),
                      reverse=True)
    if direction in {"center_out", "edge_in"}:
        center = (len(items) - 1) / 2.0
        return [
            item for _, item in sorted(
                enumerate(items),
                key=lambda pair: (abs(pair[0] - center), pair[0]),
                reverse=(direction == "edge_in"),
            )
        ]
    return items


# --------------------------------------------------------------------------- #
# 子选择
# --------------------------------------------------------------------------- #
def _normalized_order(member_ids: Sequence[object], coords: CoordMap) -> List[object]:
    """规范序：能拟合成环→角序；否则→主轴序。用于 odd_even / custom 等。"""
    present = _present(member_ids, coords)
    if len(present) < 2:
        return present
    pts = [coords[fid] for fid in present]
    fit = fit_circle(pts)
    if fit is not None:
        cx, cy, r, rms = fit
        # rms 相对半径足够小才认为是环
        if r > 1e-6 and rms / r <= 0.25:
            return order_by_angle(present, coords, cx, cy)
    return order_by_axis(present, coords)


def select_subset(member_ids: Sequence[object], coords: CoordMap,
                  kind: str, **kw) -> List[List[object]]:
    """返回一个或多个有序子集（list 内含 list）。

    kind:
      'whole'    整块，空间序（规范序）                          → [list]
      'circle'   整环角序                                        → [list]
      'arc'      角序上取连续切片                                → [list]
                 kw: arc_start_deg / arc_span_deg  或  fraction
      'line'     主轴序                                          → [list]
      'odd_even' 规范序后 [0::2] 与 [1::2] 两组呼应              → [g_even, g_odd]
      'halves'   规范序前半 / 后半（2 高 2 低式）               → [first, second]
      'custom'   给定 idx 子集（kw: idx）按形态重排             → [list]
    """
    kind = str(kind or "whole").strip().lower()
    present = _present(member_ids, coords)

    if kind == "whole":
        return [_normalized_order(present, coords)]

    if kind == "circle":
        pts = [coords[fid] for fid in present]
        fit = fit_circle(pts)
        if fit is None:
            return [_normalized_order(present, coords)]
        cx, cy, _r, _rms = fit
        return [order_by_angle(present, coords, cx, cy)]

    if kind == "line":
        return [order_by_axis(present, coords)]

    if kind == "arc":
        pts = [coords[fid] for fid in present]
        fit = fit_circle(pts)
        if fit is None:
            return [_normalized_order(present, coords)]
        cx, cy, _r, _rms = fit
        ring = order_by_angle(present, coords, cx, cy)
        if not ring:
            return [ring]
        fraction = kw.get("fraction")
        if fraction is not None:
            n = max(1, int(round(len(ring) * float(fraction))))
            start_idx = 0
            if "arc_start_deg" in kw:
                start_idx = _angle_to_index(ring, coords, cx, cy,
                                            float(kw["arc_start_deg"]))
            return [ring[start_idx:start_idx + n] if start_idx + n <= len(ring)
                    else ring[start_idx:] + ring[:(start_idx + n) % len(ring)]]
        # 角度切片
        span = float(kw.get("arc_span_deg", 120.0))
        start_deg = float(kw.get("arc_start_deg", 0.0))
        end_deg = start_deg + span
        sub: List[object] = []
        for fid in ring:
            x, y = coords[fid]
            ang = math.degrees(math.atan2(float(y) - cy, float(x) - cx)) % 360.0
            # 把角度规整到 [start_deg, start_deg+360)
            rel = (ang - start_deg) % 360.0
            if rel <= span + 1e-9:
                sub.append(fid)
        return [sub]

    if kind == "odd_even":
        order = _normalized_order(present, coords)
        return [order[0::2], order[1::2]]

    if kind == "halves":
        order = _normalized_order(present, coords)
        half = len(order) // 2
        return [order[:half], order[half:]]

    if kind == "custom":
        idx = kw.get("idx") or []
        chosen = [member_ids[i] for i in idx if 0 <= i < len(member_ids)]
        chosen = _present(chosen, coords)
        return [_normalized_order(chosen, coords)]

    # 未知 kind 退回整块
    return [_normalized_order(present, coords)]


def _angle_to_index(ring: Sequence[object], coords: CoordMap,
                    cx: float, cy: float, target_deg: float) -> int:
    """在角序环上找最接近 target_deg 的起始下标。"""
    target = math.radians(target_deg)

    def diff(fid: object) -> float:
        x, y = coords[fid]
        a = math.atan2(float(y) - cy, float(x) - cx)
        d = abs((a - target + math.pi) % math.tau - math.pi)
        return d

    best = min(range(len(ring)), key=lambda i: diff(ring[i]))
    return best
