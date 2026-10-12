# -*- coding: utf-8 -*-
"""卡片拖动的**对齐吸附纯函数**（R183 深查② #6：卡片吸附 + 对齐参考线）。

语义照节点编辑器惯例（节点编辑器同族）：被拖卡的
左/水平中心/右 与 上/垂直中心/下 共 6 条边，与同页其他卡的 6 条边两两求差，
取 ``|差| ≤ 阈值`` 的**最小者**作为吸附位移；命中的对齐位置画参考线。

本模块零 Qt 依赖（纯浮点元组进出），可无 GUI 单测：
``rect=(x, y, w, h)``（x/y = 左上角，世界坐标），``others=[(x, y, w, h), ...]``；
返回 ``(dx, dy, 竖线表, 横线表)``，竖线 ``(x, y0, y1)``、横线 ``(y, x0, x1)``。

为什么手写（先搜后写结论）：

* 现成节点库（MIT）没有独立的吸附 API —— 对齐语义埋在 ``qgraphics`` 拖动
  实现里，且库本体是 PySide 实现（0.6.44），与本仓 PyQt6 混用有风险：R183
  既定纪律是「只搬语义，不引库」（`studio_theme_nodes` 同）。
* **/ 主流 DAW** 的 snap 是**一维时间轴网格**（``int(t/step)*step``），已由
  `src/ui/timeline/timeline_snap.py` 移植；二维「边/中心对齐」与网格吸附不同族，
  无现成实现可抄。
* 本仓先例：`route_animator/grid_build.py` 的 ``SnapGrid`` 是**格点投影**吸附
  （画轨道落点用），不含边对齐/参考线。
"""

from __future__ import annotations

__all__ = ["SNAP_TOL_PX", "GUIDE_COLOR", "snap_rect", "snap_offset"]

#: 吸附阈值（逻辑像素；画布按缩放折算世界单位：`_px(SNAP_TOL_PX)`）。
SNAP_TOL_PX = 6.0
#: 参考线颜色（品红：对齐提示通用色；qascade_theme 无品红令牌，此处单点定义）。
GUIDE_COLOR = "#FF4DD2"

_EPS = 1e-6              # 浮点比较余量
_GUIDE_EPS = 0.5         # 同一参考线的归并容差（世界单位）


def _anchors(lo: float, size: float) -> tuple:
    """一维三锚点：起点 / 中心 / 终点（对齐候选边）。"""
    return lo, lo + size * 0.5, lo + size


def _axis_hits(lo: float, size: float, others: list, tol: float) -> list:
    """单轴命中表 ``[(delta, guide, o_lo, o_hi, idx)]``，按 |delta| 升序（保序）。"""
    cand = _anchors(lo, size)
    hits = []
    for o_lo, o_size, idx in others:
        for a in cand:
            for b in _anchors(o_lo, o_size):
                if abs(b - a) <= tol + _EPS:
                    hits.append((b - a, b, o_lo, o_lo + o_size, idx))
    hits.sort(key=lambda t: (abs(t[0]), t[0]))
    return hits


def _guide_list(hits: list, best, own_span: tuple, spans: list) -> list:
    """最优位移命中的全部他卡 → **一条**参考线 ``[(位置, 起, 止)]``（端点取并集）。"""
    if best is None:
        return []
    d0, g0 = best[0], best[1]
    lo, hi = own_span
    for d, g, _o_lo, _o_hi, idx in hits:
        if abs(d - d0) > _EPS or abs(g - g0) > _GUIDE_EPS:
            continue
        lo, hi = min(lo, spans[idx][0]), max(hi, spans[idx][1])
    return [(g0, lo, hi)]


def _spans(others: list, axis: int) -> list:
    """每个他卡在 ``axis``（0=水平 / 1=垂直）上的区间 ``[(lo, hi), ...]``（参考线端点）。"""
    i, j = (0, 2) if axis == 0 else (1, 3)
    return [(float(o[i]), float(o[i]) + float(o[j])) for o in others]


def snap_rect(rect, others, tol: float) -> tuple:
    """候选卡矩形 → 对齐吸附位移 + 参考线。

    返回 ``(dx, dy, v_guides, h_guides)``：``dx/dy`` 为叠加到候选矩形左上角的位移
    （无命中 → 0.0）；``v_guides=[(x, y0, y1)]`` 竖线（x 对齐）、
    ``h_guides=[(y, x0, x1)]`` 横线（y 对齐），端点为被拖卡与该他卡的并集区间。
    """
    x, y, w, h = (float(v) for v in rect)
    hits_x = _axis_hits(x, w, [(float(o[0]), float(o[2]), i) for i, o in enumerate(others)], tol)
    hits_y = _axis_hits(y, h, [(float(o[1]), float(o[3]), i) for i, o in enumerate(others)], tol)
    bx = hits_x[0] if hits_x else None
    by = hits_y[0] if hits_y else None
    dx = bx[0] if bx else 0.0
    dy = by[0] if by else 0.0
    vg = _guide_list(hits_x, bx, (y + dy, y + dy + h), _spans(others, 1)) if bx else []
    hg = _guide_list(hits_y, by, (x + dx, x + dx + w), _spans(others, 0)) if by else []
    return dx, dy, vg, hg


def snap_offset(rect, others, tol: float) -> tuple:
    """只要位移（拖动写入用）：``(dx, dy)``。"""
    dx, dy, _vg, _hg = snap_rect(rect, others, tol)
    return dx, dy
