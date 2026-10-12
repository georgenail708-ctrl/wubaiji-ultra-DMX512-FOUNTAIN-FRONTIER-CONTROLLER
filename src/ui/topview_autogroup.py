# -*- coding: utf-8 -*-
"""
topview_autogroup.py — TopView 多圆拓扑自动编组推荐
===================================================
针对「多个圆相接」这类多图案布局，给出创新性的自定义编组推荐：

  - 连续圆弧拼弧线：跨相邻圆取相接侧的弧段，连成一条长弧（扇贝形长线）。
  - 各圆同相位小弧段成排：取每个圆「前 / 后 / 上 / 下」的一小段弧，
    跨圆同相位拼起来，形成类似几排横线的效果。

约束：仅当检测到 ≥2 个圆 / 图案时才给出方案，否则返回空（需多圆布局）。
所有方案对随机种子是**确定性**的——同 seed 同结果，便于「再随机换一组」复现。

本模块只做纯几何 / 拓扑计算，不依赖 PyQt，便于独立脚本喂模拟坐标验证。
几何算子思路参考 engine/deep_aqua_core/device_atom_geometry.py（只读）。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

TWO_PI = math.tau

# 命名相位（屏幕坐标 y 向下）：
#   right=+x(0)  bottom=+y(π/2)  left=-x(π)  top=-y(3π/2)
PHASE_RIGHT = 0.0
PHASE_BOTTOM = math.pi / 2.0
PHASE_LEFT = math.pi
PHASE_TOP = 3.0 * math.pi / 2.0


# ═══════════════════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CircleInfo:
    """一个被识别出的圆 / 环图案及其归属设备。"""
    cx: float
    cy: float
    r: float
    # (fixture_id, angle)  angle ∈ [0, 2π)，以圆心为原点 atan2(y-cy, x-cx)
    members: List[Tuple[str, float]] = field(default_factory=list)

    @property
    def member_ids(self) -> List[str]:
        return [fid for fid, _ in self.members]


@dataclass
class GroupCandidate:
    """一个候选编组方案。member_ids 顺序即建议的相位铺开顺序。"""
    name: str
    description: str
    member_ids: List[str]
    strategy: str = ""          # "arc_chain" | "phase_row"
    seed: int = 0
    circle_count: int = 0

    def __len__(self) -> int:
        return len(self.member_ids)


# ═══════════════════════════════════════════════════════════════════════════
#  基础几何
# ═══════════════════════════════════════════════════════════════════════════

def _circle_from_3(p1: Tuple[float, float], p2: Tuple[float, float],
                   p3: Tuple[float, float]) -> Optional[Tuple[float, float, float]]:
    """三点定圆，返回 (cx, cy, r) 或 None（共线 / 退化）。"""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return ux, uy, r


def _ang_norm(a: float) -> float:
    """归一化到 [0, 2π)。"""
    return a % TWO_PI


def _ang_diff(a: float, b: float) -> float:
    """最小角差 a-b，落在 (-π, π]。"""
    d = (a - b) % TWO_PI
    if d > math.pi:
        d -= TWO_PI
    return d


def _ellipse_angle(x: float, y: float, cx: float, cy: float,
                   rx: float, ry: float) -> float:
    rx = rx if abs(rx) > 1e-6 else 1.0
    ry = ry if abs(ry) > 1e-6 else 1.0
    return _ang_norm(math.atan2((y - cy) / ry, (x - cx) / rx))


# ═══════════════════════════════════════════════════════════════════════════
#  圆检测
# ═══════════════════════════════════════════════════════════════════════════

def _assign_to_shapes(positions: Dict[str, Tuple[float, float]],
                      circle_shapes: Sequence[dict]) -> List[CircleInfo]:
    """已知圆 / 椭圆图案（CAD shapes）→ 把每台设备按归属圆边界就近分配。

    circle_shapes: 每个 dict 至少含 cx, cy, rx；ry 缺省=rx。
    设备若离所有圆边界都过远（> 容差）则不分配（视为非圆上设备，忽略）。
    """
    circles: List[CircleInfo] = []
    geoms: List[Tuple[float, float, float, float]] = []  # cx, cy, rx, ry
    for s in circle_shapes or []:
        try:
            cx = float(s.get("cx"))
            cy = float(s.get("cy"))
            rx = abs(float(s.get("rx", s.get("r", 0.0))))
            ry = abs(float(s.get("ry", rx)))
        except Exception:
            continue
        if rx <= 1e-3:
            continue
        if ry <= 1e-3:
            ry = rx
        geoms.append((cx, cy, rx, ry))
        circles.append(CircleInfo(cx=cx, cy=cy, r=(rx + ry) / 2.0))
    if not geoms:
        return []
    for fid, (x, y) in positions.items():
        best_i = -1
        best_res = None
        for i, (cx, cy, rx, ry) in enumerate(geoms):
            # 归一化椭圆残差：到边界的相对偏离
            nx = (x - cx) / rx
            ny = (y - cy) / ry
            res = abs(math.hypot(nx, ny) - 1.0)
            if best_res is None or res < best_res:
                best_res = res
                best_i = i
        if best_i < 0:
            continue
        cx, cy, rx, ry = geoms[best_i]
        rmean = (rx + ry) / 2.0
        # 容差：相对边界 0.35（含相接圆轻微偏离）
        if best_res is not None and best_res <= 0.35:
            ang = _ellipse_angle(x, y, cx, cy, rx, ry)
            circles[best_i].members.append((str(fid), ang))
    return [c for c in circles if c.members]


def _detect_circles_ransac(positions: Dict[str, Tuple[float, float]],
                           seed: int = 0, min_pts: int = 5,
                           iters: int = 400) -> List[CircleInfo]:
    """无显式圆图案时，用 RANSAC 从设备坐标里贪心提取多个圆。

    每轮采样 3 点定圆，统计落在半径带内的内点；取内点最多的圆，
    剔除其内点后继续，直到剩余点不足。对相接 / 同心的多圆有较好分离。
    """
    pts_all = [(str(fid), float(x), float(y)) for fid, (x, y) in positions.items()]
    if len(pts_all) < min_pts:
        return []
    rng = random.Random(int(seed) & 0x7FFFFFFF)
    remaining = list(pts_all)
    circles: List[CircleInfo] = []
    # 全局尺度，用于半径合理上限与带宽
    xs = [p[1] for p in pts_all]
    ys = [p[2] for p in pts_all]
    diag = math.hypot((max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0)
    r_max = diag * 1.2
    while len(remaining) >= min_pts:
        best_inliers: List[Tuple[str, float, float]] = []
        best_circle: Optional[Tuple[float, float, float]] = None
        pts = remaining
        npts = len(pts)
        for _ in range(iters):
            i, j, k = rng.randrange(npts), rng.randrange(npts), rng.randrange(npts)
            if i == j or j == k or i == k:
                continue
            fit = _circle_from_3((pts[i][1], pts[i][2]),
                                 (pts[j][1], pts[j][2]),
                                 (pts[k][1], pts[k][2]))
            if fit is None:
                continue
            cx, cy, r = fit
            if r <= 1e-3 or r > r_max:
                continue
            tol = max(diag * 0.02, r * 0.10)
            inliers = [p for p in pts if abs(math.hypot(p[1] - cx, p[2] - cy) - r) <= tol]
            if len(inliers) > len(best_inliers):
                best_inliers = inliers
                best_circle = (cx, cy, r)
        if best_circle is None or len(best_inliers) < min_pts:
            break
        cx, cy, r = best_circle
        # 用全部内点求质心 + 平均半径精修
        mcx = sum(p[1] for p in best_inliers) / len(best_inliers)
        mcy = sum(p[2] for p in best_inliers) / len(best_inliers)
        mr = sum(math.hypot(p[1] - mcx, p[2] - mcy) for p in best_inliers) / len(best_inliers)
        if mr <= 1e-3:
            mcx, mcy, mr = cx, cy, r
        members = [(p[0], _ang_norm(math.atan2(p[2] - mcy, p[1] - mcx))) for p in best_inliers]
        circles.append(CircleInfo(cx=mcx, cy=mcy, r=mr, members=members))
        inlier_ids = {p[0] for p in best_inliers}
        remaining = [p for p in remaining if p[0] not in inlier_ids]
    return circles


def detect_circles(positions: Dict[str, Tuple[float, float]],
                   circle_shapes: Optional[Sequence[dict]] = None,
                   seed: int = 0) -> List[CircleInfo]:
    """检测多圆归属。优先用显式 CAD 圆 / 椭圆，缺省则 RANSAC 拟合。"""
    if circle_shapes:
        circles = _assign_to_shapes(positions, circle_shapes)
        if len(circles) >= 2:
            return circles
    return _detect_circles_ransac(positions, seed=seed)


# ═══════════════════════════════════════════════════════════════════════════
#  自动编组引擎
# ═══════════════════════════════════════════════════════════════════════════

class AutoGroupEngine:
    """多圆拓扑编组推荐引擎。

    用法::

        eng = AutoGroupEngine(positions, circle_shapes=shapes)
        if eng.is_applicable():
            cands = eng.candidates(seed=0)   # 确定性候选列表
    """

    def __init__(self, positions: Dict[str, Tuple[float, float]],
                 circle_shapes: Optional[Sequence[dict]] = None,
                 scope_ids: Optional[Sequence[str]] = None):
        self.positions: Dict[str, Tuple[float, float]] = {
            str(fid): (float(x), float(y)) for fid, (x, y) in dict(positions or {}).items()
        }
        # ★ scope:只把这批设备作为编组成员(圆结构仍从全页 positions 识别)。
        #   用户要求:选中的设备来自≥2个圆即可编组——选中的常是几条弧(拟合不出完整圆),
        #   所以圆结构用整页布局识别,成员只取选中,适用性按"选中跨≥2圆"判定。
        #   scope 为空 → 全部设备都是成员(旧行为)。
        self.scope: Optional[set] = (set(str(s) for s in scope_ids) if scope_ids else None)
        # 圆检测对引擎固定（不随 reroll 改变）：种子用 0，保证稳定。
        self.circles: List[CircleInfo] = detect_circles(self.positions, circle_shapes, seed=0)

    # ── 适用性 ──────────────────────────────────────────────────────────────
    @property
    def circle_count(self) -> int:
        return len(self.circles)

    def _scoped_members(self, circle: "CircleInfo") -> List[Tuple[str, float]]:
        """该圆中落在 scope 内的成员(scope 为空则全部)。"""
        if self.scope is None:
            return list(circle.members)
        return [(fid, ang) for (fid, ang) in circle.members if fid in self.scope]

    def _circles_in_scope(self) -> int:
        """scope 实际覆盖到几个圆(成员有交集的圆)。"""
        return sum(1 for c in self.circles if self._scoped_members(c))

    def is_applicable(self) -> bool:
        """仅多圆(≥2)布局适用。有 scope 时要求选中的设备**来自≥2个圆**。"""
        if len(self.circles) < 2:
            return False
        if self.scope is None:
            return True
        return self._circles_in_scope() >= 2

    # ── 圆排序（沿圆心主轴）────────────────────────────────────────────────
    def _circle_axis_order(self) -> List[int]:
        cs = [(c.cx, c.cy) for c in self.circles]
        n = len(cs)
        if n <= 1:
            return list(range(n))
        mx = sum(p[0] for p in cs) / n
        my = sum(p[1] for p in cs) / n
        sxx = sum((p[0] - mx) ** 2 for p in cs)
        syy = sum((p[1] - my) ** 2 for p in cs)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in cs)
        if abs(sxy) < 1e-9 and abs(sxx - syy) < 1e-9:
            vx, vy = 1.0, 0.0
        else:
            ang = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
            vx, vy = math.cos(ang), math.sin(ang)
        return sorted(range(n), key=lambda i: (cs[i][0] * vx + cs[i][1] * vy, cs[i][0], cs[i][1]))

    # ── 单圆按相位窗口取成员 ────────────────────────────────────────────────
    def _members_in_window(self, circle: CircleInfo, center_phase: float,
                           half_width: float) -> List[Tuple[str, float, float]]:
        """返回窗口内 (fid, angle, signed_offset)；signed_offset 用于排序。
        ★只取 scope 内成员(scope 为空则全部):圆结构识别用全页,成员限选中。"""
        out = []
        for fid, ang in self._scoped_members(circle):
            off = _ang_diff(ang, center_phase)
            if abs(off) <= half_width + 1e-9:
                out.append((fid, ang, off))
        return out

    # ── 策略 A：连续圆弧拼弧线 ──────────────────────────────────────────────
    def _arc_chain(self, center_phase: float, half_width: float) -> List[str]:
        """跨相邻圆取同侧弧段，沿圆轴拼成一条长弧（扇贝形长线）。

        圆按主轴顺序，圆内成员按沿弧角度顺序，连接成连续走向。
        """
        order = self._circle_axis_order()
        member_ids: List[str] = []
        for ci in order:
            win = self._members_in_window(self.circles[ci], center_phase, half_width)
            # 圆内按 signed offset 排序，使弧段沿一致方向流动
            win.sort(key=lambda t: (t[2], t[1]))
            member_ids.extend(fid for fid, _, _ in win)
        return member_ids

    # ── 策略 B：各圆同相位小弧段成排 ────────────────────────────────────────
    def _phase_row(self, center_phase: float, half_width: float) -> List[str]:
        """取每个圆同一相位的一小段弧，跨圆拼成一排（横线效果）。

        圆按主轴顺序铺开，圆内按角度顺序 → 沿排方向的相位铺开序列。
        """
        order = self._circle_axis_order()
        member_ids: List[str] = []
        for ci in order:
            win = self._members_in_window(self.circles[ci], center_phase, half_width)
            win.sort(key=lambda t: (t[2], t[1]))
            member_ids.extend(fid for fid, _, _ in win)
        return member_ids

    def _circles_covered(self, member_ids: Sequence[str]) -> int:
        ids = set(member_ids)
        return sum(1 for c in self.circles if any(fid in ids for fid in c.member_ids))

    # ── 候选生成 ────────────────────────────────────────────────────────────
    def candidates(self, seed: int = 0) -> List[GroupCandidate]:
        """返回确定性候选方案列表（同 seed 同结果）。

        seed=0 给出规整的「典型」方案；seed>0 对相位 / 弧宽做确定性抖动，
        产出不同的成排 / 拼弧组合，供「再随机换一组」翻新。
        """
        if not self.is_applicable():
            return []
        rng = random.Random(int(seed) & 0x7FFFFFFF)
        # 相位抖动 + 弧宽缩放（seed=0 时为恒等）
        if seed == 0:
            phase_off = 0.0
            width_scale = 1.0
        else:
            phase_off = rng.uniform(0.0, TWO_PI)
            width_scale = rng.uniform(0.65, 1.45)

        big = (math.pi / 2.0) * width_scale       # 半弧（拼长弧）
        small = (math.pi * 0.20) * width_scale    # 小弧段（成排）

        specs = []
        # 策略 A：连续圆弧拼弧线（上 / 下 / 左 / 右 半弧）
        specs.append(("arc_chain", "连续圆弧·上弧拼接", PHASE_TOP, big))
        specs.append(("arc_chain", "连续圆弧·下弧拼接", PHASE_BOTTOM, big))
        specs.append(("arc_chain", "连续圆弧·左弧拼接", PHASE_LEFT, big))
        specs.append(("arc_chain", "连续圆弧·右弧拼接", PHASE_RIGHT, big))
        # 策略 B：各圆同相位小弧段成排（前 / 后 / 上 / 下 横线）
        specs.append(("phase_row", "同相位小弧·前段成排", PHASE_RIGHT, small))
        specs.append(("phase_row", "同相位小弧·后段成排", PHASE_LEFT, small))
        specs.append(("phase_row", "同相位小弧·上段成排", PHASE_TOP, small))
        specs.append(("phase_row", "同相位小弧·下段成排", PHASE_BOTTOM, small))

        if seed > 0:
            rng.shuffle(specs)

        out: List[GroupCandidate] = []
        seen_sets: set = set()
        for strat, name, phase, half in specs:
            cphase = _ang_norm(phase + phase_off)
            if strat == "arc_chain":
                ids = self._arc_chain(cphase, half)
            else:
                ids = self._phase_row(cphase, half)
            if len(ids) < 3:
                continue
            covered = self._circles_covered(ids)
            if covered < 2:
                continue
            key = frozenset(ids)
            if key in seen_sets:
                continue
            seen_sets.add(key)
            out.append(GroupCandidate(
                name=name,
                description=f"{name}：{len(ids)} 台 / 跨 {covered} 圆",
                member_ids=ids,
                strategy=strat,
                seed=seed,
                circle_count=self.circle_count,
            ))
        return out


def recommend(positions: Dict[str, Tuple[float, float]],
              circle_shapes: Optional[Sequence[dict]] = None,
              seed: int = 0) -> List[GroupCandidate]:
    """便捷入口：构造引擎并返回候选方案（不适用时为空列表）。"""
    return AutoGroupEngine(positions, circle_shapes).candidates(seed=seed)
