# -*- coding: utf-8 -*-
"""表格视图的**重绘范围决策**（纯计算；从 `table_view` 拆出以守 ≤500 行门禁）。

为什么需要它（2026-09-14 实测的性能回归）
────────────────────────────────────────
`table_value_merge.dirty_rect_for` 用同一个返回值 `None` 表达**两种截然不同**
的情形：

    ① 调用方**没给** `refreshed_keys`（无从收窄）→ 只能保守整视口重绘；
    ② 给了键、但**没有一个格真正变化**（含仅分歧/来源未翻转）→ 应当**完全不重绘**。

原 `update_values` 把 `dirty is None` 一律当成①：于是快表（100ms）在**一字未变**
的 tick 上也整视口重绘。实测（`_offline_render_work/_probe_tableview_paint_20260914.py`）：

    · 98 格表   idle tick p50 ≈ 10.2ms   （1 格真变化时仅 0.32ms）
    · 360 格表  idle tick p50 ≈ 31.4ms   （1 格真变化时仅 0.34ms）

即"没变化"反而比"有变化"贵 **30~90 倍**，且 idle 时**每 tick 都付** —— GUI 线程
被无谓的全量重绘占用，正是用户"格子刷新还是一卡一卡"（交互被挤）的来源。

本模块给三分决策 `(need_repaint, rect)`，语义与 `dirty_rect_for` **完全一致**
（直接复用其 `_KeyDiff` 变化判据，杜绝两套"是否变化"逻辑分叉）：
  · `(False, None)`            → 零变化，**不要调用 update()**
  · `(True, rect is not None)` → 只重画 `rect`（变化格的并集）
  · `(True, None)`             → 整视口重绘（未收窄 / 变化格缺矩形 → 保守）
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple

from .table_value_merge import _KeyDiff

Key = Tuple[str, str]


def plan_update(incoming: dict, old: dict, rects: Dict[Key, Any], *,
                refreshed_keys: Optional[Set[Key]] = None
                ) -> Tuple[bool, Optional[Any]]:
    """→ `(是否需要重绘, 只重绘的矩形或 None(整视口))`。

    `incoming`/`old` = `{"values": ..., "spreads": ...}`（与 `dirty_rect_for` 同）。
    `refreshed_keys` 为空/None → 无从收窄 → `(True, None)`（整视口）。
    """
    if not refreshed_keys:
        return True, None
    diff = _KeyDiff(incoming, old, rects)
    out = None
    for k in refreshed_keys:
        if not diff.changed(k):
            continue
        r = rects.get(k)
        if r is None:
            return True, None       # 变化格缺矩形：保守整视口，绝不漏画
        out = r if out is None else out.united(r)
    if out is None:
        return False, None          # ★ 本 tick 无任何可视变化 → 零重绘
    return True, out


def update_scope(need: bool, dirty: Optional[Any], marks_changed: bool, *,
                 viewport_rect) -> Optional[Any]:
    """把 `plan_update` 结果落成"要传给 `QWidget.update()` 的矩形"。

    → `None` = **不要调用 update()**（零变化）；否则是脏区矩形、或在未收窄/缺矩形时
    回退的整视口矩形（惰性调用 `viewport_rect()`，避免无谓的 mapFrom）。
    """
    if not (need or marks_changed):
        return None
    if dirty is not None and not dirty.isEmpty():
        return dirty
    return viewport_rect()


__all__ = ["plan_update", "update_scope"]
