# -*- coding: utf-8 -*-
"""R13 回并（merge）面板侧：把选中的「子列」并回其主列车。

引擎侧 `merge_trains(a, b)` 是唯一真源（尾部对齐 nose_s/edge、链长表拼接、
trail 顺接、被并段摘除）；本模块只挑前/后段、刷状态与树——与
`runner_panel._fleet_detach_selected` 的"选中即操作"口径对称。

前后段判定：子列 id 形如 ``<母id>#detach#k#n`` / ``<母id>#uncouple#节点``，
`#` 前即母 id（与 `runner_panel._base_train_key` / `_sync_train_cards` 同口径）。
只选子列时前段自动取母列；母列自身不在引擎里（被并过）则跳过该对。
"""
from __future__ import annotations

import logging
from typing import List, Tuple

log = logging.getLogger(__name__)


def base_of(tid: str) -> str:
    """分队 id → 母列 id（`#` 前段；未分队原样返回）。"""
    return str(tid).split("#", 1)[0] or str(tid)


def _selected_train_ids(panel) -> List[str]:
    try:
        return [str(t) for t in panel._fleet_selected_train_ids()]
    except Exception:
        log.debug("merge: fleet selection read failed", exc_info=True)
        return []


def merge_plan(panel) -> List[Tuple[str, str]]:
    """选中集 → [(front_id, rear_id), ...]（只含前后段都还在引擎里的对）。

    子列并回其母列；母列本身不是"要并的东西"（无 `#` 的行跳过）；选中序即
    合并序（多子列依次并入同一前段）。
    """
    trains = getattr(getattr(panel, "_engine", None), "_trains", None) or {}
    out: List[Tuple[str, str]] = []
    for tid in _selected_train_ids(panel):
        if "#" not in tid or tid not in trains:
            continue
        front = base_of(tid)
        if front in trains:
            out.append((front, tid))
    return out


def merge_selected_units(panel) -> int:
    """把选中子列依次并回前段；返回成功合并数（无选中/非法 → 0）。"""
    pairs = merge_plan(panel)
    manual = getattr(panel, "_manual_merge_pairs", None)
    if not pairs and manual:
        pairs = [(str(a), str(b)) for a, b in manual]
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return 0
    done = 0
    for front, rear in pairs:
        if front in engine._trains and rear in engine._trains:
            if engine.merge_trains(front, rear):
                done += 1
    return done


__all__ = ["base_of", "merge_plan", "merge_selected_units"]
