# -*- coding: utf-8 -*-
"""画布排布纯函数（R183 W2-ST #7 自动布局 + #8 对齐/分布）—— 零 Qt，可无 GUI 单测。

**为什么手写（先搜后写结论）**：

* 参考库 `auto_layout`（MIT，已核实
  ）内部依赖其
  QGraphicsScene 节点几何 + networkx 可选加速；本仓无 networkx（**不新增依赖**），
  且图是纯 dict 列表 —— 按既定纪律只搬语义：Kahn 拓扑分层用 **stdlib** 实现。
* 对齐/分布：Qt 无内置（`QLayout` 只管子控件），本仓无现成；专业节点编辑器的
  「边缘/中心对齐 + 等距分布」语义直搬，实现 ≈60 行。

**分层语义**（照「按依赖拓扑分层」）：仅用 `from→to` 依赖边；层号 = 从各源点出发的
**最长路径**（同层 = 可并行）；层内按节点原表顺序稳定排布；x = 层 × 列距，
y = 层内序 × 行距。**有环 → 返回 None**（调用方给提示不动图，不静默半成品）。

对齐语义（照节点编辑器惯例）：以选中集外包围盒为准 —— 左对齐到 `min(x)`、右对齐到
`max(x+w)`、上/下同理；分布 = 首末卡固定，其余按中心等距重排（`distribute_axis`
按包围盒长边自动选横/纵）。
"""
from __future__ import annotations

__all__ = ["ALIGN_MODES", "ALIGN_LABELS", "DIST_X", "DIST_Y",
           "kahn_layers", "auto_layout", "align_deltas", "distribute_deltas",
           "distribute_axis"]

#: 对齐模式（值 → 中文；单键 Ctrl+L/R/T/B 分派）。
ALIGN_MODES = ("left", "right", "top", "bottom")
ALIGN_LABELS = {"left": "左对齐", "right": "右对齐", "top": "上对齐", "bottom": "下对齐"}
#: 分布轴。
DIST_X = "x"
DIST_Y = "y"

_EPS = 1e-9


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _graph_edges(node_ids, edges) -> tuple | None:
    """建邻接与入度；自环/未知端点/畸形项处理完 → ``(adj, indeg)``，自环 → None。"""
    known = set(node_ids)
    indeg = {n: 0 for n in node_ids}
    adj: dict = {n: [] for n in node_ids}
    for e in edges or ():
        try:
            src, dst = str(e[0]), str(e[1])
        except (TypeError, IndexError):
            continue
        if src not in known or dst not in known:
            continue
        if src == dst:
            return None                 # 自环 = 环
        adj[src].append(dst)
        indeg[dst] += 1
    return adj, indeg


def _peel(ids, adj, indeg) -> dict | None:
    """Kahn 剥层：返回 ``{nid: 层号}``；剥不完（有环）→ None。"""
    layer = {n: 0 for n in ids}
    ready = [n for n in ids if indeg[n] == 0]
    seen = 0
    while ready:
        nid = ready.pop(0)
        seen += 1
        for dst in adj[nid]:
            layer[dst] = max(layer[dst], layer[nid] + 1)
            indeg[dst] -= 1
            if indeg[dst] == 0:
                ready.append(dst)
    return layer if seen == len(ids) else None


def kahn_layers(node_ids, edges) -> list | None:
    """Kahn 拓扑分层：返回 ``[[层0 节点…], [层1…], …]``；**有环 → None**。

    `edges` = 依赖边 ``(src, dst)`` 二元组序列（自环直接判环）；层内保持
    `node_ids` 原顺序（确定性输出）；未知端点忽略。
    """
    ids = [str(n) for n in node_ids]
    built = _graph_edges(ids, edges)
    if built is None:
        return None
    layer = _peel(ids, built[0], built[1])
    if layer is None:
        return None
    out = [[] for _ in range(max(layer.values(), default=-1) + 1)]
    for nid in ids:
        out[layer[nid]].append(nid)
    return out


def auto_layout(nodes, edges, x_gap: float = 300.0, y_gap: float = 150.0,
                origin=(60.0, 60.0)) -> dict | None:
    """自动布局：``{nid: (x, y)}``；有环 → None（调用方提示不动）。

    `nodes` 用 dict 列表（取 id），`edges` 用 dict 列表（取 from/to）；其余畸形项
    容错忽略。x = 层号 × `x_gap` + `origin[0]`，y = 层内序 × `y_gap` + `origin[1]`
    （origin = 左上锚点，调用方通常传现有内容的左上角）。
    """
    node_ids = [str(n.get("id")) for n in nodes or []
                if isinstance(n, dict) and n.get("id")]
    if not node_ids:
        return {}
    pairs = [(e.get("from"), e.get("to")) for e in edges or () if isinstance(e, dict)]
    layers = kahn_layers(node_ids, pairs)
    if layers is None:
        return None
    ox, oy = (float(origin[0]), float(origin[1])) if isinstance(origin, (tuple, list)) \
        else (float(origin), float(origin))
    out = {}
    for li, group in enumerate(layers):
        for ri, nid in enumerate(group):
            out[nid] = (ox + li * float(x_gap), oy + ri * float(y_gap))
    return out


def _box(rects) -> tuple:
    xs = [_f(r[0]) for r in rects]
    ys = [_f(r[1]) for r in rects]
    x1 = max(_f(r[0]) + _f(r[2]) for r in rects)
    y1 = max(_f(r[1]) + _f(r[3]) for r in rects)
    return min(xs), min(ys), x1, y1


def align_deltas(rects, mode: str) -> list:
    """对齐位移表 ``[(dx, dy), …]``（与 `rects` 同序）；模式非法/空表 → 全 0。

    `rects` = ``[(x, y, w, h), …]``（世界坐标左上角 + 宽高）。
    """
    zeros = [(0.0, 0.0)] * len(rects or ())
    if not rects or mode not in ALIGN_MODES:
        return zeros
    x0, y0, x1, y1 = _box(rects)
    out = []
    for r in rects:
        x, y, w, h = _f(r[0]), _f(r[1]), _f(r[2]), _f(r[3])
        if mode == "left":
            out.append((x0 - x, 0.0))
        elif mode == "right":
            out.append((x1 - (x + w), 0.0))
        elif mode == "top":
            out.append((0.0, y0 - y))
        else:
            out.append((0.0, y1 - (y + h)))
    return out


def distribute_axis(rects) -> str:
    """分布轴：包围盒长边（横向跨度 ≥ 纵向 → x，否则 y）；空表 → x。"""
    if not rects:
        return DIST_X
    x0, y0, x1, y1 = _box(rects)
    return DIST_X if (x1 - x0) >= (y1 - y0) else DIST_Y


def distribute_deltas(rects, axis: str = DIST_X) -> list:
    """等距分布位移表：首末卡固定，其余按中心等距；<3 张 → 全 0。"""
    zeros = [(0.0, 0.0)] * len(rects or ())
    if len(rects or ()) < 3:
        return zeros
    horizontal = axis != DIST_Y
    centers = [(i, _f(r[0] if horizontal else r[1]) + _f(r[2] if horizontal else r[3]) * 0.5)
               for i, r in enumerate(rects)]
    order = sorted(centers, key=lambda t: (t[1], t[0]))
    first_c, last_c = order[0][1], order[-1][1]
    step = (last_c - first_c) / (len(order) - 1)
    out = [(0.0, 0.0)] * len(rects)
    for slot, (idx, c) in enumerate(order):
        shift = (first_c + slot * step) - c
        out[idx] = (shift, 0.0) if horizontal else (0.0, shift)
    return out
