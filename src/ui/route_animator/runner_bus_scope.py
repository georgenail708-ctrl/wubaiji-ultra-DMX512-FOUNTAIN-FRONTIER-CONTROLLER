# -*- coding: utf-8 -*-
"""跑动编排上报调度台时的**逐列车覆盖集**归集（模块级，见 R141 缺陷⑩）。

缺陷：`runner_panel._bus_nodes` 在多列火车时把每个节点的 `fixture_ids` 直接置成
`[]`（原注释"按车头位置近似归集"其实没实现）。后果：
  · 窗格显示"覆盖 0 台"，用户看不出跑动在驱动哪些设备；
  · 存进调度台后 `scene_takeover` 的收窄判据拿不到范围 → stamp 作用域失真，
    与 effect / programmer（都上报真实范围）不一致。

修法：**复用引擎自己的覆盖判定**（`_edge_projection` + `_apply_car_coverage`）对
每一列火车单独算一遍覆盖设备集 —— 不复制圆覆盖几何、不新增第二套真源。
单独成文件的原因：`runner_panel.py` 已 4k+ 行（架构门禁只许减），新逻辑写模块级函数。
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def _device_ids(engine) -> list:
    """引擎当前页已注册的设备 id（唯一对外口径，`route_mask_map()` 同源）。"""
    try:
        return list(getattr(engine, "_device_ids", []) or [])
    except Exception:
        return []


def _car_usable(engine, car, edges) -> object:
    """取车厢所在边；几何/半径不成立时返回 None（与引擎 tick 同判据）。"""
    edge = edges.get(getattr(car, "edge_id", None))
    if edge is None or edge.length <= 0.0:
        return None
    if float(getattr(car, "radius", 0.0) or 0.0) <= 0.0:
        return None
    if float(getattr(car, "car_length", 0.0) or 0.0) <= 0.0:
        return None
    return edge


def _accumulate_train_coverage(engine, train, out, order_s) -> None:
    """把该列车的车厢覆盖并进 `out`/`order_s`（逐车厢，异常不中断整列）。"""
    edges = getattr(engine, "_edges", None)
    if not isinstance(edges, dict):
        return
    for car in (getattr(train, "cars", []) or []):
        edge = _car_usable(engine, car, edges)
        if edge is None:
            continue
        try:
            engine._edge_projection(edge)          # 几何缓存（引擎内部幂等）
            engine._apply_car_coverage(car, edge, out, order_s)
        except Exception:
            logger.debug("route: per-train coverage failed", exc_info=True)


def train_coverage_ids(engine, train) -> list:
    """该列车**自己**当前覆盖的设备 id 列表（保序，与 `active_ordered_ids` 同口径）。

    直接调用引擎的 `_apply_car_coverage`：它内部就是"以车厢为圆心的圆形激活区 +
    最近边归属"的唯一定义；这里只是把输出容器收窄到单列火车，故数值与整帧覆盖一致。
    """
    ids = _device_ids(engine)
    if not ids or train is None:
        return []
    out = np.zeros(len(ids), dtype=np.uint8)
    order_s = np.full(len(ids), np.inf, dtype=np.float64)
    _accumulate_train_coverage(engine, train, out, order_s)
    nz = np.flatnonzero(out >= 1)
    if nz.size == 0:
        return []
    nz = nz[np.argsort(order_s[nz], kind="stable")]
    return [ids[int(i)] for i in nz.tolist()]


def covered_ids_for(engine, train, fallback: list) -> list:
    """取该列车覆盖集；逐列不可得时回落到整帧覆盖集（单列火车的既有语义）。"""
    try:
        ids = train_coverage_ids(engine, train)
    except Exception:
        logger.debug("route: covered_ids_for failed", exc_info=True)
        ids = []
    if ids:
        return ids
    return list(fallback or [])


def _route_effect_node(ctx: dict, tid, train):
    """把一列火车转成调度台节点（`ctx` 见 `build_route_nodes`）。"""
    from ...processing.dispatch import EffectNode
    engine = ctx["engine"]
    last = ctx["last"]
    ids = covered_ids_for(engine, train, last if ctx["multi"] is False else [])
    name = str(ctx["names"].get(tid, "") or "") or "跑动火车"
    mode = str(getattr(train, "mode", "") or "")
    return EffectNode(
        panel="route", track_type=str(tid), group_key=ctx["group_key"](ids),
        group_name=name, fixture_ids=ids, effect_id="", category="",
        layers=["clip_playback"],
        effect_name=str(ctx["names"].get(tid, "") or tid),
        summary=(f"覆盖 {len(ids)} 台 · 模式 {mode}" if ids else f"模式 {mode}"),
        live=ctx["playing"],
    )


def build_route_nodes(ctx: dict) -> list:
    """把当前车列覆盖转成调度台窗格（一列火车一节点，不去重）。

    `ctx` = {engine, trains, names, last, playing, multi, group_key}。

    ★ R130（用户实报"已经没有启用了…在输出那边看不到却被存新效果时存进去"）：
      本面板"输出" = 注册的覆盖遮罩；释放/复位都清遮罩 → 输出侧逐字节无影响。
      "只有车列定义而无遮罩"时不得上报（门槛在调用方 `_bus_nodes` 里判，保持不动）。

    ★★ R141 缺陷⑩（多列火车作用域为空）：原先 `ids = last if len(trains)==1 else []`
      → 多列时每格 `fixture_ids` 恒为空，窗格显示"覆盖 0 台"、存进调度台后 stamp
      作用域失真。现按**每列自己的覆盖集**归集；单列仍回落到整帧覆盖集。
    """
    trains = dict(ctx.get("trains") or {})
    ctx = dict(ctx)
    ctx["multi"] = len(trains) > 1
    return [_route_effect_node(ctx, tid, tr) for tid, tr in trains.items()]


__all__ = ["build_route_nodes", "covered_ids_for", "train_coverage_ids"]

