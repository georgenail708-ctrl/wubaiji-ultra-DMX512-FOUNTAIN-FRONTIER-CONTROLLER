"""Pure track-smoothing math for the route animator.

Self-contained geometry helpers that denoise a hand-drawn route, decimate it to
control points, enforce a minimum curvature radius, and re-interpolate it with a
centripetal Catmull-Rom spline.  This module imports only the standard library
math module; it must never import Qt or any other repository module.
"""

from __future__ import annotations

import math
import os
from bisect import bisect_right
from typing import Iterator, List, Sequence, Tuple

import numpy as np

Point = Tuple[float, float]
Polyline = List[Point]

TWO_PI = 2.0 * math.pi
_EPS = 1e-12


# ── numba 门控（R183 3.3 计算层批）───────────────────────────────────────────
# 离线几何（rdp / catmull_rom，motor_path._slew_clamp 同门）njit 加速 ~百倍；
# `QASCADE_NUMBA=0`（或 false/off/no，沿用 QASCADE_C_KERNELS 等门惯例）整体退
# 纯 Python——两条路径同一算法文本，金标对拍逐位一致（tests/test_r183_compute_golden.py）。
def _env_enabled(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "off", "no")


try:
    if _env_enabled("QASCADE_NUMBA"):
        from numba import njit as _njit
    else:
        _njit = None
except Exception:  # pragma: no cover - numba 缺失/损坏时静默回退
    _njit = None

NUMBA_ACTIVE = _njit is not None


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def _pt_dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _cumlen(pts: Sequence[Sequence[float]]) -> List[float]:
    """Cumulative arc length from pts[0] to each vertex (cum[0] == 0)."""
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + _pt_dist(pts[i - 1], pts[i]))
    return cum


def _point_at_arclen(
    pts: Sequence[Sequence[float]], cum: Sequence[float], s: float
) -> Point:
    """Interpolated point at arc length s along an open polyline (clamped)."""
    if len(pts) == 1 or cum[-1] <= _EPS:
        return (float(pts[0][0]), float(pts[0][1]))
    s = max(0.0, min(s, cum[-1]))
    i = bisect_right(cum, s) - 1
    i = max(0, min(i, len(pts) - 2))
    seg = cum[i + 1] - cum[i]
    t = (s - cum[i]) / seg if seg > _EPS else 0.0
    t = max(0.0, min(1.0, t))
    ax, ay = pts[i][0], pts[i][1]
    bx, by = pts[i + 1][0], pts[i + 1][1]
    return (ax + (bx - ax) * t, ay + (by - ay) * t)


def _loop_point_at(
    pts: Sequence[Sequence[float]],
    cum: Sequence[float],
    total: float,
    s: float,
) -> Point:
    """Point at arc length s on a closed loop, wrapping modulo total."""
    s = s % total
    if s <= cum[-1] + _EPS:
        return _point_at_arclen(pts, cum, s)
    seg = total - cum[-1]
    t = (s - cum[-1]) / seg if seg > _EPS else 0.0
    ax, ay = pts[-1][0], pts[-1][1]
    bx, by = pts[0][0], pts[0][1]
    return (ax + (bx - ax) * t, ay + (by - ay) * t)


def _circumradius(
    p: Sequence[float], q: Sequence[float], r: Sequence[float]
) -> float:
    """Radius of the circumcircle through three points (inf if degenerate)."""
    ax, ay = p[0], p[1]
    bx, by = q[0], q[1]
    cx, cy = r[0], r[1]
    a = math.hypot(bx - cx, by - cy)
    b = math.hypot(ax - cx, ay - cy)
    c = math.hypot(ax - bx, ay - by)
    area2 = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    if area2 < _EPS:
        return float("inf")
    return a * b * c / (2.0 * area2)


def _turn_angle(
    a: Sequence[float], b: Sequence[float], c: Sequence[float]
) -> float:
    """Signed turning angle at b from edge a->b to edge b->c."""
    v1x, v1y = b[0] - a[0], b[1] - a[1]
    v2x, v2y = c[0] - b[0], c[1] - b[1]
    return math.atan2(v1x * v2y - v1y * v2x, v1x * v2x + v1y * v2y)


def _turn_angles(
    pts: Sequence[Sequence[float]], closed: bool
) -> Iterator[float]:
    """Yield the per-vertex signed turning angles in traversal order."""
    n = len(pts)
    if n < 3:
        return
    if closed:
        m = n - 1 if _pt_dist(pts[0], pts[-1]) < 1e-9 else n
        for i in range(m):
            a = pts[(i - 1) % m]
            b = pts[i]
            c = pts[(i + 1) % m]
            yield _turn_angle(a, b, c)
    else:
        for i in range(1, n - 1):
            yield _turn_angle(pts[i - 1], pts[i], pts[i + 1])


def _lerp(
    a: Point, b: Point, ta: float, tb: float, t: float
) -> Point:
    span = tb - ta
    if abs(span) < _EPS:
        return a
    u = (t - ta) / span
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


def _cr_point(
    p0: Point,
    p1: Point,
    p2: Point,
    p3: Point,
    t0: float,
    t1: float,
    t2: float,
    t3: float,
    t: float,
) -> Point:
    """Centripetal Catmull-Rom interpolation at parameter t in [t1, t2]."""
    a1 = _lerp(p0, p1, t0, t1, t)
    a2 = _lerp(p1, p2, t1, t2, t)
    a3 = _lerp(p2, p3, t2, t3, t)
    b1 = _lerp(a1, a2, t0, t2, t)
    b2 = _lerp(a2, a3, t1, t3, t)
    return _lerp(b1, b2, t1, t2, t)


def _cr_segment(
    p0: Point, p1: Point, p2: Point, p3: Point, per_seg: int, alpha: float = 0.5
) -> List[Point]:
    d01 = max(_pt_dist(p1, p0), _EPS)
    d12 = max(_pt_dist(p2, p1), _EPS)
    d23 = max(_pt_dist(p3, p2), _EPS)
    t0 = 0.0
    t1 = t0 + d01 ** alpha
    t2 = t1 + d12 ** alpha
    t3 = t2 + d23 ** alpha
    out: List[Point] = []
    for j in range(per_seg):
        t = t1 + (t2 - t1) * j / per_seg
        out.append(_cr_point(p0, p1, p2, p3, t0, t1, t2, t3, t))
    return out


def resample(pts: Sequence[Sequence[float]], step: float) -> Polyline:
    """Equal arc-length resampling of a polyline with spacing step."""
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 2:
        return pts
    cum = _cumlen(pts)
    total = cum[-1]
    if total <= _EPS or step <= 0.0:
        return pts
    count = int(total / step)
    out: Polyline = []
    for idx in range(count + 1):
        out.append(_point_at_arclen(pts, cum, idx * step))
    if _pt_dist(out[-1], pts[-1]) > 1e-9:
        out.append(pts[-1])
    return out


def moving_average(pts: Sequence[Sequence[float]], k: int, closed: bool) -> Polyline:
    """Sliding-average denoising; closed uses cyclic indices (i +/- k) % n.

    R183 3.3 计算层批：双层 Python 循环 → scipy.ndimage.uniform_filter1d
    （懒导入；roadmap 实测 2.44ms → 40×）。边界 mode 语义核对：
    - closed：循环窗口 (i±k)%n 恰 2k+1 样本 ≡ mode="wrap" 完整窗口均值；
    - open：旧实现按**实际窗口数**截断除；uniform_filter1d mode="constant"
      （补零）是"和含零、除以全长 size"，故 sum = out×w 后按逐点实际窗口
      数 cnt 重除 → 与截断均值同语义。
    求和顺序与旧逐点 sum() 不同，差异在 ~1e-13 相对量级（金标容差 1e-9
    绝对值，见 tests/test_r183_compute_golden.py）。
    """
    pts = [(float(x), float(y)) for x, y in pts]
    n = len(pts)
    if n == 0 or k <= 0:
        return pts
    from scipy.ndimage import uniform_filter1d
    arr = np.asarray(pts, dtype=np.float64)
    w = 2 * int(k) + 1
    if closed:
        sm = uniform_filter1d(arr, size=w, axis=0, mode="wrap")
    else:
        ssum = uniform_filter1d(arr, size=w, axis=0, mode="constant") * w
        idx = np.arange(n)
        cnt = np.minimum(idx + k, n - 1) - np.maximum(idx - k, 0) + 1
        sm = ssum / cnt[:, None]
    return list(zip(sm[:, 0].tolist(), sm[:, 1].tolist()))


def _rdp_keep_arrays(axs: np.ndarray, ays: np.ndarray, eps: float) -> List[bool]:
    """rdp 的保留标记（纯 Python + numpy 批量距离，2026-08 版原实现复件）。

    QASCADE_NUMBA=0 的退路，亦是金标对拍参照：每轮分段内距离扫描用 numpy
    批量完成，保留区间内到基线最远且 > eps 的点，递归两段（LIFO 栈）。
    """
    n = axs.shape[0]
    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True
    stack: List[Tuple[int, int]] = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        ax, ay = axs[start], ays[start]
        dx, dy = axs[end] - ax, ays[end] - ay
        seg2 = dx * dx + dy * dy
        mx = axs[start + 1:end]
        my = ays[start + 1:end]
        if seg2 <= _EPS:
            d = np.hypot(mx - ax, my - ay)
        else:
            t = ((mx - ax) * dx + (my - ay) * dy) / seg2
            t = np.clip(t, 0.0, 1.0)
            d = np.hypot((mx - ax) - t * dx, (my - ay) - t * dy)
        dmax = float(d.max()) if d.size else -1.0
        if dmax > eps:
            idx = start + 1 + int(np.argmax(d))
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))
    return keep


if _njit is not None:
    @_njit(cache=True)
    def _rdp_seg_far(axs, ays, ax, ay, ctx):
        """开区间 (start,end) 内到基线最远的点（距离 + 首达索引，同 np.argmax 首达）。

        ctx = (dx, dy, seg2, start, end)：门禁限 5 个位置参数，参数打包为元组
        （njit 下等价寄存器传值）。"""
        dx, dy, seg2, start, end = ctx
        dmax = -1.0
        didx = start
        for j in range(start + 1, end):
            mx = axs[j]
            my = ays[j]
            if seg2 <= _EPS:
                dd = math.hypot(mx - ax, my - ay)
            else:
                t = ((mx - ax) * dx + (my - ay) * dy) / seg2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                dd = math.hypot((mx - ax) - t * dx, (my - ay) - t * dy)
            if dd > dmax:
                dmax = dd
                didx = j
        return dmax, didx

    @_njit(cache=True)
    def _rdp_keep_kernel(axs, ays, eps):
        """_rdp_keep_arrays 的 numba 内核：同一栈序（LIFO、先压 (start,idx) 后
        压 (idx,end) → 先弹后者）、同一 FP 表达式（numba math.hypot 与
        np.hypot 同为 ucrt hypot）→ 输出逐位一致。栈用预分配 int64 数组
        （numba nopython 不收 list[tuple]，见 compute-adoption.md）。"""
        n = axs.shape[0]
        keep = np.zeros(n, dtype=np.bool_)
        keep[0] = True
        keep[n - 1] = True
        stack = np.empty((2 * n + 2, 2), dtype=np.int64)
        sp = 0
        stack[sp, 0] = 0
        stack[sp, 1] = n - 1
        sp += 1
        while sp > 0:
            sp -= 1
            start = stack[sp, 0]
            end = stack[sp, 1]
            if end <= start + 1:
                continue
            ax = axs[start]
            ay = ays[start]
            dx = axs[end] - ax
            dy = ays[end] - ay
            seg2 = dx * dx + dy * dy
            dmax, didx = _rdp_seg_far(
                axs, ays, ax, ay, (dx, dy, seg2, start, end))
            if dmax > eps:
                keep[didx] = True
                stack[sp, 0] = start
                stack[sp, 1] = didx
                sp += 1
                stack[sp, 0] = didx
                stack[sp, 1] = end
                sp += 1
        return keep
else:
    _rdp_keep_kernel = None


def rdp(pts: Sequence[Sequence[float]], eps: float) -> Polyline:
    """Open-chain Ramer-Douglas-Peucker decimation to control points.

    R183 3.3 计算层批：距离扫描与栈调度移入 numba njit(cache=True) 内核
    （QASCADE_NUMBA=0 时走 _rdp_keep_arrays 纯 Python 退路，两路逐位一致，
    复验 roadmap 107× 之测）。语义不变：保留区间内到基线最远且 > eps 的点。
    """
    pts = [(float(x), float(y)) for x, y in pts]
    n = len(pts)
    if n < 3 or eps <= 0.0:
        return pts
    axs = np.asarray([p[0] for p in pts], dtype=np.float64)
    ays = np.asarray([p[1] for p in pts], dtype=np.float64)
    if _rdp_keep_kernel is not None:
        keep_arr = _rdp_keep_kernel(axs, ays, float(eps))
        return [pts[i] for i in range(n) if keep_arr[i]]
    keep = _rdp_keep_arrays(axs, ays, float(eps))
    return [pts[i] for i in range(n) if keep[i]]


def rdp_closed(pts: Sequence[Sequence[float]], eps: float) -> Polyline:
    """Closed-loop RDP: break at the farthest-from-centroid vertex, then open RDP.

    Running open-chain RDP directly on a stored-closed loop (first == last)
    collapses the ring because the baseline has zero length; breaking at an
    extreme vertex turns the ring into a genuine open chain with a non-zero
    chord baseline.
    """
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 3:
        return pts
    loop = pts[:-1] if _pt_dist(pts[0], pts[-1]) < 1e-9 else list(pts)
    if len(loop) < 3:
        return loop
    cx = sum(p[0] for p in loop) / len(loop)
    cy = sum(p[1] for p in loop) / len(loop)
    far = 0
    far_d = -1.0
    for i, (x, y) in enumerate(loop):
        d = (x - cx) ** 2 + (y - cy) ** 2
        if d > far_d:
            far_d = d
            far = i
    rotated = loop[far:] + loop[:far]
    return rdp(rotated, eps)


def curvature_radius_at(
    pts: Sequence[Sequence[float]], i: int, h: float, closed: bool,
    cum: Sequence[float] | None = None, total: float | None = None,
) -> float:
    """Arc-length-template curvature radius at vertex i.

    Takes the vertex itself plus the points h arc-length before and after it
    along the polyline, then returns the circumcircle radius of those three
    points.  Adjacent-dense-point circumcircles are deliberately avoided because
    their area term underflows (previously measured an R=60 circle as 4.68).

    性能: cum/total 由调用方预计算并复用(避免每顶点 O(n) 重算)。
    """
    n = len(pts)
    if n < 3 or h <= 0.0 or not (0 <= i < n):
        return float("inf")
    p = (float(pts[i][0]), float(pts[i][1]))
    if cum is None:
        cum = _cumlen(pts)
    if cum[-1] <= _EPS:
        return float("inf")
    s_i = cum[i]
    if closed:
        if total is None:
            total = cum[-1] + _pt_dist(pts[-1], pts[0])
        if total <= _EPS:
            return float("inf")
        before = _loop_point_at(pts, cum, total, s_i - h)
        after = _loop_point_at(pts, cum, total, s_i + h)
    else:
        before = _point_at_arclen(pts, cum, s_i - h)
        after = _point_at_arclen(pts, cum, s_i + h)
    return _circumradius(before, p, after)


def min_radius(
    pts: Sequence[Sequence[float]], h: float, closed: bool,
    max_samples: int = 0,
) -> float:
    """Minimum curvature radius over the polyline's vertices.

    max_samples>0 时按等距采样上限检查(长轨道校验用; 0=全量, 与旧语义一致)。
    """
    n = len(pts)
    if n < 3:
        return float("inf")
    cum = _cumlen(pts)
    total = cum[-1] + (_pt_dist(pts[-1], pts[0]) if closed else 0.0)
    idxs: Iterator[int]
    if max_samples > 0 and n > max_samples:
        step = (n - 1) / (max_samples - 1)
        idxs = (int(round(k * step)) for k in range(max_samples))
    else:
        idxs = range(n)
    best = float("inf")
    for i in idxs:
        r = curvature_radius_at(pts, i, h, closed, cum, total)
        if r < best:
            best = r
    return best


def _adjacent_radius(
    pts: Sequence[Sequence[float]], i: int, closed: bool
) -> float:
    """Circumcircle radius of the (i-1, i, i+1) triple; inf for degenerate/collinear.

    Control polygons are sparse, so adjacent-triple circumcircles are the right
    local curvature measure here (the +-h arc-length template is for dense
    curves and must not be applied to sparse control points).
    """
    n = len(pts)
    if n < 3:
        return float("inf")
    if closed:
        a = pts[(i - 1) % n]
        b = pts[(i + 1) % n]
    else:
        if i == 0 or i == n - 1:
            return float("inf")
        a = pts[i - 1]
        b = pts[i + 1]
    p = pts[i]
    return _circumradius(a, p, b)


def _laplacian_clamp(
    ctrl: Sequence[Sequence[float]], rmin: float, closed: bool, iters: int
) -> Polyline:
    """Laplacian smoothing of control vertices whose adjacent-triple radius < rmin."""
    pts = [[float(x), float(y)] for x, y in ctrl]
    n = len(pts)
    if n < 3 or rmin <= 0.0 or iters <= 0:
        return [(x, y) for x, y in pts]
    lam = 0.35
    for _ in range(iters):
        radii = [_adjacent_radius(pts, i, closed) for i in range(n)]
        moved = False
        for i in range(n):
            if radii[i] >= rmin:
                continue
            if closed:
                a = pts[(i - 1) % n]
                b = pts[(i + 1) % n]
            else:
                if i == 0 or i == n - 1:
                    continue
                a = pts[i - 1]
                b = pts[i + 1]
            mx = (a[0] + b[0]) * 0.5
            my = (a[1] + b[1]) * 0.5
            pts[i][0] += lam * (mx - pts[i][0])
            pts[i][1] += lam * (my - pts[i][1])
            moved = True
        if not moved:
            break
    return [(x, y) for x, y in pts]


def clamp_min_radius(
    ctrl: Sequence[Sequence[float]],
    rmin: float,
    closed: bool,
    iters: int = 40,
) -> Polyline:
    """Enforce a minimum curvature radius on the control polygon.

    Catmull-Rom interpolation tightens corners below the control polygon's own
    radius, so the clamp verifies the *densified* curve (per_seg=12) measured
    with the +-8 arc-length template and boosts the internal target by x1.25
    per round (max 6 rounds) until the final curve meets rmin. Returns the
    best-effort result if the iteration budget runs out.
    """
    if len(ctrl) < 3 or rmin <= 0.0:
        return list(ctrl)
    target = float(rmin)
    best: Polyline = [(float(x), float(y)) for x, y in ctrl]
    for _ in range(6):
        cand = _laplacian_clamp(ctrl, target, closed, iters)
        dense = catmull_rom(cand, 12, closed)
        if len(dense) >= 3 and min_radius(dense, 8.0, closed, max_samples=256) >= rmin:
            return cand
        best = cand
        target *= 1.25
    return best


def _catmull_rom_python(ctrl: Sequence[Sequence[float]], per_seg: int,
                        closed: bool) -> Polyline:
    """catmull_rom 纯 Python 复件（QASCADE_NUMBA=0 退路 + 金标参照）。"""
    pts = [(float(x), float(y)) for x, y in ctrl]
    n = len(pts)
    per_seg = max(1, int(per_seg))
    out: Polyline = []
    if closed:
        for i in range(n):
            p0 = pts[(i - 1) % n]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n]
            out.extend(_cr_segment(p0, p1, p2, p3, per_seg))
    else:
        for i in range(n - 1):
            p0 = pts[i - 1] if i > 0 else pts[0]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else pts[n - 1]
            out.extend(_cr_segment(p0, p1, p2, p3, per_seg))
    return out


if _njit is not None:
    @_njit(cache=True)
    def _cr_lerp(a, b, ta, tb, t):
        """_lerp 语义的 numba 版：span < _EPS 取 a 端，否则线性插值（返回 x, y）。

        a/b 为 (x, y) 元组（门禁限 5 个位置参数；njit 下等价寄存器传值）。"""
        span = tb - ta
        if abs(span) < _EPS:
            return a[0], a[1]
        u = (t - ta) / span
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u

    @_njit(cache=True)
    def _catmull_rom_kernel(pcoords, tparams, per_seg):
        """_cr_point 三层 lerp 的 numba 内核（纯 +-*/，IEEE 严格序）。

        向心参数化 d**alpha 的 d 用 math.hypot——CPython 的 math.hypot/pow 是
        自带精确算法，与 numba(LLVM→ucrt) 可差 1 ulp（实测 300k 随机对 16.6%
        不等），故 t0..t3 由包装层按 CPython 语义预算后传入（逐位一致），
        内核只做与 Python 完全同序的插值算术。"""
        m = pcoords.shape[0]
        out = np.empty((m * per_seg, 2), dtype=np.float64)
        for i in range(m):
            p0 = (pcoords[i, 0, 0], pcoords[i, 0, 1])
            p1 = (pcoords[i, 1, 0], pcoords[i, 1, 1])
            p2 = (pcoords[i, 2, 0], pcoords[i, 2, 1])
            p3 = (pcoords[i, 3, 0], pcoords[i, 3, 1])
            t0 = tparams[i, 0]
            t1 = tparams[i, 1]
            t2 = tparams[i, 2]
            t3 = tparams[i, 3]
            for j in range(per_seg):
                t = t1 + (t2 - t1) * j / per_seg
                a1x, a1y = _cr_lerp(p0, p1, t0, t1, t)
                a2x, a2y = _cr_lerp(p1, p2, t1, t2, t)
                a3x, a3y = _cr_lerp(p2, p3, t2, t3, t)
                b1x, b1y = _cr_lerp((a1x, a1y), (a2x, a2y), t0, t2, t)
                b2x, b2y = _cr_lerp((a2x, a2y), (a3x, a3y), t1, t3, t)
                fx, fy = _cr_lerp((b1x, b1y), (b2x, b2y), t1, t2, t)
                out[i * per_seg + j, 0] = fx
                out[i * per_seg + j, 1] = fy
        return out
else:
    _catmull_rom_kernel = None


def catmull_rom(
    ctrl: Sequence[Sequence[float]], per_seg: int, closed: bool
) -> Polyline:
    """Centripetal Catmull-Rom (alpha=0.5) densification of control points.

    R183 3.3 计算层批：内层插值循环移入 numba njit(cache=True) 内核
    （QASCADE_NUMBA=0 走 _catmull_rom_python 退路，两路逐位一致）。
    每段向心参数化（math.hypot/pow）在包装层按 CPython 语义预算——
    numba 的 hypot/pow 与 CPython 可差 1 ulp（实测在案），参数预算每段
    仅 3 次 hypot（m 段），占输出 n*per_seg 的 O(1/n)。
    """
    pts = [(float(x), float(y)) for x, y in ctrl]
    n = len(pts)
    if n < 2:
        return pts
    per_seg = max(1, int(per_seg))
    if _catmull_rom_kernel is None:
        return _catmull_rom_python(pts, per_seg, closed)
    m = n if closed else n - 1
    pcoords = np.empty((m, 4, 2), dtype=np.float64)
    tparams = np.empty((m, 4), dtype=np.float64)
    for i in range(m):
        if closed:
            p0 = pts[(i - 1) % n]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n]
        else:
            p0 = pts[i - 1] if i > 0 else pts[0]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else pts[n - 1]
        d01 = max(_pt_dist(p1, p0), _EPS)
        d12 = max(_pt_dist(p2, p1), _EPS)
        d23 = max(_pt_dist(p3, p2), _EPS)
        t0 = 0.0
        t1 = t0 + d01 ** 0.5
        t2 = t1 + d12 ** 0.5
        t3 = t2 + d23 ** 0.5
        pcoords[i, 0, 0], pcoords[i, 0, 1] = p0
        pcoords[i, 1, 0], pcoords[i, 1, 1] = p1
        pcoords[i, 2, 0], pcoords[i, 2, 1] = p2
        pcoords[i, 3, 0], pcoords[i, 3, 1] = p3
        tparams[i, 0], tparams[i, 1] = t0, t1
        tparams[i, 2], tparams[i, 3] = t2, t3
    out = _catmull_rom_kernel(pcoords, tparams, per_seg)
    return list(zip(out[:, 0].tolist(), out[:, 1].tolist()))


def total_turn(pts: Sequence[Sequence[float]], closed: bool) -> float:
    """Cumulative signed turning angle (radians) along the polyline."""
    return sum(_turn_angles(pts, closed))


def collapse_laps(
    pts: Sequence[Sequence[float]], closed: bool
) -> Tuple[Polyline, int]:
    """Repeated-loop folding: collapse a multi-lap route to one lap plus a count.

    laps is round(|total_turn| / 2*pi).  With fewer than two laps the input is
    returned unchanged with laps == 1; otherwise the polyline is truncated at
    the first point where the accumulated turning angle reaches one full turn.
    """
    turn = abs(total_turn(pts, closed))
    laps = int(round(turn / TWO_PI))
    if laps < 2:
        return (list(pts), 1)
    n = len(pts)
    acc = 0.0
    if closed:
        m = n - 1 if _pt_dist(pts[0], pts[-1]) < 1e-9 else n
        for i in range(m):
            a = pts[(i - 1) % m]
            b = pts[i]
            c = pts[(i + 1) % m]
            acc += _turn_angle(a, b, c)
            if abs(acc) >= TWO_PI - 1e-9:
                return (list(pts[: i + 1]), laps)
    else:
        for i in range(1, n - 1):
            acc += _turn_angle(pts[i - 1], pts[i], pts[i + 1])
            if abs(acc) >= TWO_PI - 1e-9:
                return (list(pts[: i + 1]), laps)
    return (list(pts), laps)


def smooth_track(
    raw: Sequence[Sequence[float]],
    snap_tol: float = 12.0,
    min_radius: float = 25.0,
    per_seg: int = 12,
) -> Tuple[Polyline, bool, int]:
    """Full smoothing pipeline; returns (points, closed, lap_count).

    Pipeline: equal-arc resampling (step 4.0), closure detection, lap folding
    (closed only), moving average (k=2), RDP (eps=2.0), minimum-radius clamping,
    and centripetal Catmull-Rom densification (per_seg points/segment).  Closed
    results always end with a copy of points[0] so downstream engines can
    detect closure by exact equality.

    per_seg: 长轨道可调小以控制点数；面板会对真实工程长链自动降密。
    """
    if len(raw) < 2:
        return (list(raw), False, 1)
    pts = resample(raw, 4.0)
    if len(pts) < 2:
        return (list(raw), False, 1)
    closed = _pt_dist(pts[0], pts[-1]) < snap_tol
    pts = moving_average(pts, 2, closed)
    pts = moving_average(pts, 2, closed)  # 第二遍: 单遍后残留曲率仍低于基准(E6)
    # 圈数折叠必须在去抖之后: 带噪闭环会自交使绕数虚高, 累计转角把圆误截成弧
    lap_count = 1
    if closed:
        pts, lap_count = collapse_laps(pts, True)
    pts = rdp_closed(pts, 2.0) if closed else rdp(pts, 2.0)
    pts = clamp_min_radius(pts, min_radius, closed, iters=40)
    pts = catmull_rom(pts, max(2, int(per_seg)), closed)
    if closed and pts:
        pts = pts + [pts[0]]
    return (pts, closed, lap_count)

