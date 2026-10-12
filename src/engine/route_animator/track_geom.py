# -*- coding: utf-8 -*-
"""route_animator.track_geom — 折线几何纯函数（R183 W2 从 runner_engine 原样搬出）。

住址变更为让 `runner_engine.py` 腾出行数容纳 R8（A-B 段循环）/ R13（解挂回并），
门禁对已超基线文件要求"只减不增"。**函数体逐行未改**（含 `_LOOP_MARKER_CAP` 与
自交检测的网格哈希注释），`runner_engine` 反向 import 保持旧名字可见
（`runner_engine._detect_self_intersections` 等仍可用），既有调用方零迁移。

无 Qt / 无 UI 依赖：engine 层不得反向 import src.ui（CLAUDE.md §10.2）。
"""
from __future__ import annotations

import math
from bisect import bisect_left

import numpy as np

#: 自相交标记封顶: 超长轨道不把画布/内存拖垮
_LOOP_MARKER_CAP = 200


def _polyline_cumlen(path: list[tuple[float, float]]) -> list[float]:
    """路径各顶点的累积弧长(cum[0]=0), 与 CE 内核 sqrt(dx*dx+dy*dy) 逐位一致。"""
    n = len(path)
    if n <= 128:
        cum = [0.0]
        for i in range(1, n):
            dx = path[i][0] - path[i - 1][0]
            dy = path[i][1] - path[i - 1][1]
            cum.append(cum[-1] + math.sqrt(dx * dx + dy * dy))
        return cum
    arr = np.asarray(path, dtype=np.float64)
    dx = arr[1:, 0] - arr[:-1, 0]
    dy = arr[1:, 1] - arr[:-1, 1]
    seg = np.sqrt(dx * dx + dy * dy)
    return [0.0] + np.cumsum(seg).tolist()


def _point_at_arclen(s: float, path: list[tuple[float, float]],
                     cum: list[float]) -> tuple[float, float]:
    """折线上弧长 *s* 处的插值点(越界钳制到端点)。"""
    if not path:
        return (0.0, 0.0)
    if len(path) == 1 or cum[-1] <= 0.0:
        return path[0]
    s = max(0.0, min(s, cum[-1]))
    i = _seg_index(cum, s)
    seg = cum[i + 1] - cum[i]
    t = (s - cum[i]) / seg if seg > 1e-9 else 0.0
    ax, ay = path[i]
    bx, by = path[i + 1]
    return (ax + (bx - ax) * t, ay + (by - ay) * t)


def _seg_index(cum: list[float], s: float) -> int:
    """弧长 s 所在线段下标(钳到 [0, len(cum)-2])。"""
    i = bisect_left(cum, s) - 1
    if i < 0:
        return 0
    last = len(cum) - 2
    return i if i <= last else last


def _tangent_at_arclen(s: float, path: list[tuple[float, float]],
                       cum: list[float]) -> tuple[float, float]:
    """折线弧长 *s* 处的单位切向量(越界钳制到端点)。"""
    if not path or len(path) < 2 or cum[-1] <= 0.0:
        return (1.0, 0.0)
    s = max(0.0, min(s, cum[-1]))
    i = _seg_index(cum, s)
    dx = path[i + 1][0] - path[i][0]
    dy = path[i + 1][1] - path[i][1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-12 else (1.0, 0.0)


def _tangent_angle(s: float, path: list[tuple[float, float]],
                   cum: list[float]) -> float:
    """折线弧长 *s* 处的切线朝向角(弧度, atan2)。"""
    tx, ty = _tangent_at_arclen(s, path, cum)
    return math.atan2(ty, tx)


def _segment_intersection_t(a, b, c, d):
    """线段 ab 与 cd 的交点参数 (t, u); 无交(含平行/共线)返回 None。

    仅报告严格内部相交(0<t<1 且 0<u<1), 端点触碰不算自交。
    """
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-12:
        return None
    qpx, qpy = c[0] - a[0], c[1] - a[1]
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if 0.0 < t < 1.0 and 0.0 < u < 1.0:
        return (t, u)
    return None


def _segment_cells(seg_bbox: list[tuple[float, float, float, float]],
                   cell: float) -> dict[tuple[int, int], list[int]]:
    """把每条线段按包围盒插入其覆盖的网格单元（相交线段必共享单元）。"""
    cells: dict[tuple[int, int], list[int]] = {}
    for i, (x0, y0, x1, y1) in enumerate(seg_bbox):
        for cx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for cy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                cells.setdefault((cx, cy), []).append(i)
    return cells


def _bbox_overlap(b1, b2) -> bool:
    """两包围盒是否相交（分离轴判据；不相交则跳过精确判交）。"""
    return not (max(b1[0], b2[0]) > min(b1[2], b2[2])
                or max(b1[1], b2[1]) > min(b1[3], b2[3]))


def _pair_hit(points, cum, i: int, j: int, seg_bbox) -> dict | None:
    """一对线段的交点记录（包围盒分离/平行/端点触碰 → None）。"""
    if not _bbox_overlap(seg_bbox[i], seg_bbox[j]):
        return None
    hit = _segment_intersection_t(
        points[i], points[i + 1], points[j], points[j + 1])
    return None if hit is None else _hit_record(points, cum, i, j, hit)


def _pair_pending(bucket, ai: int, seen) -> int:
    """下一个未判过的线段对下标（-1 = 本桶判完）。"""
    i = bucket[ai]
    for b in range(ai + 1, len(bucket)):
        if bucket[b] > i + 1 and (i, bucket[b]) not in seen:
            return b
    return -1


def _bucket_pairs(bucket, seen):
    """逐个产出本桶内所有待判线段对 (i, j)（相邻段/已判对跳过）。"""
    for a in range(len(bucket)):
        b = _pair_pending(bucket, a, seen)
        while b >= 0:
            seen.add((bucket[a], bucket[b]))
            yield (bucket[a], bucket[b])
            b = _pair_pending(bucket, a, seen)


def _scan_cell_hits(cells, points, cum, seg_bbox) -> list[dict]:
    """同单元线段对定点判交（相邻段与已判过对跳过）；命中数达封顶即停。"""
    seen: set[tuple[int, int]] = set()
    hits: list[dict] = []
    for bucket in cells.values():
        for i, j in _bucket_pairs(bucket, seen):
            rec = _pair_hit(points, cum, i, j, seg_bbox)
            if rec is None:
                continue
            hits.append(rec)
            if len(hits) >= _LOOP_MARKER_CAP:
                return hits
    return hits


def _hit_record(points, cum, i: int, j: int, hit) -> dict:
    """交点 → {s_a, s_b, x, y}（两条线段的弧长位置与交点世界坐标）。"""
    t, u = hit
    return {
        "s_a": cum[i] + t * (cum[i + 1] - cum[i]),
        "s_b": cum[j] + u * (cum[j + 1] - cum[j]),
        "x": points[i][0] + t * (points[i + 1][0] - points[i][0]),
        "y": points[i][1] + t * (points[i + 1][1] - points[i][1]),
    }


def _detect_self_intersections(points: list[tuple[float, float]]) -> list[dict]:
    """折线自相交检测。返回交点记录列表, 每条 = {s_a, s_b, x, y}。

    相邻段(共享端点)不判交; 命中点供 UI 画「绕圈标记」。
    算法: 网格哈希 —— 每条线段按包围盒插入其覆盖的所有网格单元,
    只对同单元内的线段对判交(任意相交对必共享单元), 复杂轨道 O(n) 级。
    标记数封顶 _LOOP_MARKER_CAP, 避免超长轨道的标记把画布拖垮。
    """
    n = len(points)
    if n < 4:
        return []
    cum = _polyline_cumlen(points)
    # 单元边长 = 中位段长(按总长/段数估计), 保证多数线段只跨 1~2 个单元
    total = cum[-1] if cum else 0.0
    cell = max(total / max(1, n - 1), 1e-6)
    seg_bbox: list[tuple[float, float, float, float]] = []
    for i in range(n - 1):
        ax, ay = points[i]
        bx, by = points[i + 1]
        x0, x1 = (ax, bx) if ax < bx else (bx, ax)
        y0, y1 = (ay, by) if ay < by else (by, ay)
        seg_bbox.append((x0, y0, x1, y1))
    return _scan_cell_hits(_segment_cells(seg_bbox, cell), points, cum, seg_bbox)


def _wrap_spans(lo: float, hi: float, length: float,
                closed: bool) -> list[tuple[float, float]]:
    """把绝对弧区间 [lo,hi] 切成 [0,length) 内的 1~2 段。

    开链: 夹取(原语义); 闭环: 环绕切分(E4 已证与 wrap 真值 97 个位置逐字节一致)。
    """
    if length <= 0.0 or hi <= lo:
        return []
    if not closed:
        a = lo if lo > 0.0 else 0.0
        b = hi if hi < length else length
        return [(a, b)] if b > a else []
    if hi - lo >= length:
        return [(0.0, length)]
    a = lo % length
    b = hi % length
    if a <= b:
        return [(a, b)]
    return [(a, length), (0.0, b)]


def _sub_polyline(points: list[tuple[float, float]], cum: list[float],
                  s0: float, s1: float) -> list[tuple[float, float]]:
    """截取折线 [s0, s1] 弧区间的子折线(端点插值)。"""
    if not points:
        return []
    total = cum[-1] if cum else 0.0
    s0 = max(0.0, min(s0, total))
    s1 = max(0.0, min(s1, total))
    if s1 <= s0:
        return [tuple(_point_at_arclen(s0, points, cum))]
    sub = [_point_at_arclen(s0, points, cum)]
    for i in range(1, len(points)):
        cs = cum[i]
        if s0 < cs < s1:
            sub.append(tuple(points[i]))
    sub.append(_point_at_arclen(s1, points, cum))
    out: list[tuple[float, float]] = []
    for p in sub:
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    return out


__all__ = ["_LOOP_MARKER_CAP", "_polyline_cumlen", "_point_at_arclen",
           "_tangent_at_arclen", "_tangent_angle", "_segment_intersection_t",
           "_detect_self_intersections", "_wrap_spans", "_sub_polyline"]
